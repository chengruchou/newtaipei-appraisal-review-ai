"""Isolated unit regressions using fixture material, not real-document acceptance."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from appraisal_review.api.app import create_app
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode
from appraisal_review.domain.workbench_contracts import CaseContextView, ReviewSessionView
from appraisal_review.testing.job_store_contract import NOW
from tests.unit.test_human_task_api import Mounted, StubResolver
from tests.unit.test_human_task_service import Harness, caller, confirming, fact_task


class StoredReadAccess:
    """Explicit isolated port substitute; does not grant real source admission."""

    def __init__(self, harness):
        self.harness = harness
        self.fail = False
        self.reads = 0
        self.final_reads = 0
        self.on_read = None

    async def read(self, principal, reference):
        self.reads += 1
        if self.fail:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if self.on_read is not None:
            await self.on_read()
        snapshot = await self.harness.tasks.read_snapshot(revision=reference)
        if snapshot is None:
            raise ServiceFault(ServiceErrorCode.NOT_FOUND)
        return snapshot

    async def require_current(self, principal, reference):
        self.final_reads += 1
        if self.fail:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)


@asynccontextmanager
async def mounted(*, viewer=None, access_enabled=True, assessment=None):
    harness = Harness()
    task = fact_task(harness.snapshot, harness.run_id)
    await harness.setup((task,), principal=caller())
    harness.jobs._runs[
        (harness.job_id, harness.run_id)
    ].revision = harness.snapshot.revision.reference
    access = StoredReadAccess(harness)
    service = HumanTaskService(
        harness.tasks,
        clock=lambda: NOW,
        new_revision_id=lambda: "r2",
        workbench_access=access if access_enabled else None,
        assessment_reader=assessment,
    )
    harness.service = service
    harness.access = access
    app = create_app(
        human_task_service=service, principal_resolver=StubResolver(viewer or caller())
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://service") as client:
        yield Mounted(client=client, harness=harness, task=task)


def test_authenticated_session_is_the_resolved_actor_only():
    async def scenario():
        async with mounted() as env:
            response = await env.client.get("/v1/review-session")
            assert response.status_code == 200
            session = ReviewSessionView.model_validate(response.json())
            assert session.actor == caller().actor
            assert set(response.json()) == {
                "schema_version",
                "actor",
                "data_mode",
                "configured_jobs",
            }

    asyncio.run(scenario())


def test_context_projects_pinned_rules_without_paths_or_grants():
    async def scenario():
        async with mounted() as env:
            response = await env.client.get(f"/v1/review-jobs/{env.harness.job_id}/context")
            assert response.status_code == 200
            context = CaseContextView.model_validate(response.json())
            material = env.harness.snapshot.material
            assert context.documents == env.harness.snapshot.revision.documents
            assert context.identity == material.policy.identity
            assert context.revision == env.harness.snapshot.revision.reference
            assert (
                tuple(rule.reference for rule in context.rules)
                == env.harness.snapshot.revision.rules
            )
            assert all(selection.status == "unique" for selection in context.selections)
            assert context.rules[0].evidence == tuple(material.policy.rule_sets[0].evidence)
            assert "source_file" not in response.text
            assert "document_uri" not in response.text
            assert "authorization" not in response.text

    asyncio.run(scenario())


def test_receipt_query_never_writes_and_recovers_exact_original_after_confirmation():
    async def scenario():
        async with mounted() as env:
            key = "read-receipt-key"
            path = f"/v1/review-tasks/{env.task.task_id}/responses"
            before = await env.client.get(path + "/" + key)
            assert before.status_code == 404
            current = await env.client.get(f"/v1/review-tasks/{env.task.task_id}")
            assert current.json()["task"]["state"] == "open"
            body = confirming(env.harness, env.task, key=key).model_dump(mode="json")
            committed = await env.client.post(path, json=body)
            assert committed.status_code == 200
            for _ in range(2):
                recovered = await env.client.get(path + "/" + key)
                assert recovered.status_code == 200
                assert recovered.json() == committed.json()
            assert (await env.client.get(path + "/other-key")).status_code == 404
            assert (
                await env.client.get(f"/v1/review-tasks/{uuid4()}/responses/{key}")
            ).status_code == 404
            revisions = await env.client.get(f"/v1/review-jobs/{env.harness.job_id}/revisions")
            assert len(revisions.json()["revisions"]) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("suffix", ["context", "tasks", "revisions"])
def test_other_actor_cannot_read_case_metadata(suffix):
    async def scenario():
        async with mounted(viewer=caller("another-person")) as env:
            response = await env.client.get(f"/v1/review-jobs/{env.harness.job_id}/{suffix}")
            assert response.status_code == 404
            assert set(response.json()) == {"schema_version", "code", "message"}

    asyncio.run(scenario())


def test_other_actor_cannot_query_receipts():
    async def scenario():
        async with mounted(viewer=caller("another-person")) as env:
            response = await env.client.get(
                f"/v1/review-tasks/{env.task.task_id}/responses/known-key"
            )
            assert response.status_code == 404

    asyncio.run(scenario())


def test_receipt_path_validation_is_sanitized():
    async def scenario():
        async with mounted() as env:
            response = await env.client.get("/v1/review-tasks/private-invalid-id/responses/x")
            assert response.status_code == 422
            assert response.json()["code"] == "invalid_request"
            assert "private-invalid-id" not in response.text

    asyncio.run(scenario())


@pytest.mark.parametrize("endpoint", ["context", "receipt"])
def test_projection_requires_explicit_current_source_authority(endpoint):
    async def scenario():
        async with mounted(access_enabled=False) as env:
            path = (
                f"/v1/review-jobs/{env.harness.job_id}/context"
                if endpoint == "context"
                else f"/v1/review-tasks/{env.task.task_id}/responses/unknown-key"
            )
            response = await env.client.get(path)
            assert response.status_code == 503
            assert response.json()["code"] == "capability_unavailable"
            assert "observations" not in response.json()

    asyncio.run(scenario())


@pytest.mark.parametrize("endpoint", ["context", "receipt", "assessment"])
def test_current_source_denial_hides_all_projection_content(endpoint):
    async def assessment(run, snapshot):
        raise AssertionError("Denied sources must not reach the review reader")

    async def scenario():
        async with mounted(assessment=assessment) as env:
            env.harness.access.fail = True
            path = (
                f"/v1/review-tasks/{env.task.task_id}/responses/unknown-key"
                if endpoint == "receipt"
                else f"/v1/review-jobs/{env.harness.job_id}/{endpoint}"
            )
            response = await env.client.get(path)
            assert response.status_code == 403
            assert set(response.json()) == {"schema_version", "code", "message"}

    asyncio.run(scenario())


def test_missing_material_is_not_reported_as_an_empty_preparation_case():
    async def scenario():
        async with mounted() as env:
            env.harness.tasks._snapshots.clear()
            response = await env.client.get(f"/v1/review-jobs/{env.harness.job_id}/context")
            assert response.status_code == 404
            assert "identity" not in response.json()

    asyncio.run(scenario())


def test_revision_advancing_during_material_read_is_a_conflict():
    async def scenario():
        async with mounted() as env:

            async def move():
                env.harness.jobs._runs[
                    (env.harness.job_id, env.harness.run_id)
                ].revision = env.harness.snapshot.revision.reference.model_copy(
                    update={"revision_id": "later"}
                )

            env.harness.access.on_read = move
            response = await env.client.get(f"/v1/review-jobs/{env.harness.job_id}/context")
            assert response.status_code == 409
            assert response.json()["code"] == "version_conflict"

    asyncio.run(scenario())


def test_receipt_read_rechecks_authority_after_the_receipt_store_await():
    async def scenario():
        async with mounted() as env:
            body = confirming(env.harness, env.task, key="recorded").model_dump(mode="json")
            path = f"/v1/review-tasks/{env.task.task_id}/responses"
            assert (await env.client.post(path, json=body)).status_code == 200
            original = env.harness.tasks.read_receipt

            async def revoke(**kwargs):
                value = await original(**kwargs)
                env.harness.access.fail = True
                return value

            env.harness.tasks.read_receipt = revoke
            response = await env.client.get(path + "/recorded")
            assert response.status_code == 403
            assert "consumed_version" not in response.json()

    asyncio.run(scenario())


def test_assessment_returns_only_the_exact_stored_run_and_rechecks_after_read():
    from appraisal_review.domain.workbench_contracts import PausedReviewView

    async def scenario():
        access = None

        async def assessment(run, snapshot):
            assert snapshot.revision.reference == run.revision
            assert access is not None
            access.fail = True
            return PausedReviewView(
                run=run,
                status="needs_review",
                findings=(),
                coverage={"required": ["road_width"], "verified": [], "missing": ["road_width"]},
                verification=None,
            )

        async with mounted(assessment=assessment) as env:
            access = env.harness.access
            response = await env.client.get(f"/v1/review-jobs/{env.harness.job_id}/assessment")
            assert response.status_code == 403
            assert "findings" not in response.json()

    asyncio.run(scenario())
