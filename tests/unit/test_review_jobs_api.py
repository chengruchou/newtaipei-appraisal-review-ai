"""HTTP boundary for durable review jobs.

Two properties matter most here and are asserted repeatedly: a submission returns before
any review runs, and a job belonging to someone else is indistinguishable from a job that
does not exist.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from appraisal_review.adapters.local.job_store import InMemoryJobStore, InMemoryResultStore
from appraisal_review.api.app import create_app
from appraisal_review.application.job_state import JobEvent, JobPolicy
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    Permission,
    ReviewSubmission,
    RunReference,
    ServiceProblem,
    ServiceResult,
)
from appraisal_review.testing.job_store_contract import NOW, principal, submission

CLOCK = [NOW]


@dataclass
class StubResolver:
    """Stands in for the adapter-bound authenticator; a body never asserts identity."""

    principal: Principal

    async def current_principal(self) -> Principal:
        return self.principal


@dataclass
class Harness:
    client: TestClient
    service: ReviewJobService
    store: InMemoryJobStore
    results: InMemoryResultStore


def build(caller: Principal | None = None) -> Harness:
    CLOCK[0] = NOW
    store = InMemoryJobStore()
    results = InMemoryResultStore()
    service = ReviewJobService(
        store,
        results,
        policy=JobPolicy(),
        clock=lambda: CLOCK[0],
        jitter=lambda bound: 0,
    )
    app = create_app(job_service=service, principal_resolver=StubResolver(caller or principal()))
    return Harness(TestClient(app), service, store, results)


def body(payload: ReviewSubmission | None = None) -> dict[str, object]:
    return (payload or submission()).model_dump(mode="json")


def claim(harness: Harness, job_id: str, run_id: str) -> object:
    return asyncio.run(
        harness.service.claim(job_id=UUID(job_id), run_id=UUID(run_id), owner=uuid4())
    )


class TestSubmission:
    def test_a_submission_is_accepted_without_running_a_review(self) -> None:
        harness = build()
        response = harness.client.post("/v1/review-jobs", json=body())
        assert response.status_code == 202
        payload = response.json()
        # 202 carries acceptance of durable responsibility and no result of any kind.
        assert payload["job_status"] == JobStatus.QUEUED.value
        assert set(payload) == {"schema_version", "job", "run", "job_status"}
        assert "result" not in payload and "findings" not in payload

    def test_acceptance_never_advertises_an_attempt(self) -> None:
        harness = build()
        run = harness.client.post("/v1/review-jobs", json=body()).json()["run"]
        assert run["attempt_id"] is None
        assert run["runtime_session_id"] is None

    def test_an_exact_replay_reports_the_real_status_instead_of_a_second_acceptance(
        self,
    ) -> None:
        harness = build()
        first = harness.client.post("/v1/review-jobs", json=body())
        job_id = first.json()["job"]["job_id"]
        run_id = first.json()["run"]["run_id"]
        claim(harness, job_id, run_id)
        replay = harness.client.post("/v1/review-jobs", json=body())
        # Answering 202 "queued" for a job that is already running would be fabricated.
        assert replay.status_code == 200
        assert replay.json()["job_status"] == JobStatus.RUNNING.value
        assert replay.json()["job"]["job_id"] == job_id

    def test_a_reused_key_with_a_changed_payload_conflicts(self) -> None:
        harness = build()
        harness.client.post("/v1/review-jobs", json=body())
        response = harness.client.post("/v1/review-jobs", json=body(submission(material="e")))
        assert response.status_code == 409
        assert response.json()["code"] == "version_conflict"

    def test_a_submission_for_another_case_is_refused(self) -> None:
        harness = build()
        response = harness.client.post(
            "/v1/review-jobs", json=body(submission(case_id="case-someone-else"))
        )
        assert response.status_code == 403
        assert response.json()["code"] == "unauthorized"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"idempotency_key": "k"},
            {"revision": "private-document-text", "documents": [], "idempotency_key": "k"},
        ],
    )
    def test_an_invalid_submission_returns_the_sanitized_envelope(
        self, payload: dict[str, object]
    ) -> None:
        harness = build()
        response = harness.client.post("/v1/review-jobs", json=payload)
        assert response.status_code == 422
        # No loc/msg/input echo: a rejected payload can quote document text.
        assert set(response.json()) == {"schema_version", "code", "message"}
        assert response.json()["code"] == "invalid_request"

    def test_a_submission_missing_a_permission_is_refused_before_any_write(self) -> None:
        caller = Principal(
            actor=principal().actor,
            case_ids=frozenset({"case-contract"}),
            permissions=frozenset({Permission.CONFIRM}),
        )
        harness = build(caller)
        assert harness.client.post("/v1/review-jobs", json=body()).status_code == 403
        assert asyncio.run(harness.store.pending_dispatches(now=NOW, limit=5)) == ()


class TestStatusAndResult:
    def test_status_reports_the_durable_job_status(self) -> None:
        harness = build()
        job_id = harness.client.post("/v1/review-jobs", json=body()).json()["job"]["job_id"]
        response = harness.client.get(f"/v1/review-jobs/{job_id}")
        assert response.status_code == 200
        assert response.json()["job_status"] == JobStatus.QUEUED.value
        assert response.json()["result_version"] == 0
        assert response.json()["problem"] is None

    def test_the_status_view_exposes_no_storage_or_worker_detail(self) -> None:
        harness = build()
        job_id = harness.client.post("/v1/review-jobs", json=body()).json()["job"]["job_id"]
        payload = harness.client.get(f"/v1/review-jobs/{job_id}").json()
        assert set(payload) == {
            "schema_version",
            "job",
            "job_status",
            "current_run",
            "attempt_count",
            "result_version",
            "cancel_requested",
            "open_task_ids",
            "problem",
        }
        serialized = harness.client.get(f"/v1/review-jobs/{job_id}").text
        for leaked in ("s3://", "https://", "arn:aws", "lease_owner", "dispatch_token"):
            assert leaked not in serialized

    def test_the_result_route_conflicts_until_something_is_committed(self) -> None:
        harness = build()
        job_id = harness.client.post("/v1/review-jobs", json=body()).json()["job"]["job_id"]
        response = harness.client.get(f"/v1/review-jobs/{job_id}/result")
        assert response.status_code == 409
        assert response.json()["code"] == "version_conflict"

    def test_a_committed_result_is_returned_after_publication(self) -> None:
        harness = build()
        accepted = harness.client.post("/v1/review-jobs", json=body()).json()
        job_id, run_id = accepted["job"]["job_id"], accepted["run"]["run_id"]
        attempt = claim(harness, job_id, run_id)
        published = ServiceResult(
            run=RunReference(
                run_id=UUID(run_id),
                revision=submission().revision,
                attempt_id=attempt.attempt_id,  # type: ignore[attr-defined]
            ),
            result_version=1,
            execution_status=ExecutionStatus.SUCCEEDED,
            business_status=WorkflowStatus.NEEDS_REVIEW,
        )
        asyncio.run(harness.service.publish(attempt, published))  # type: ignore[arg-type]
        response = harness.client.get(f"/v1/review-jobs/{job_id}/result")
        assert response.status_code == 200
        assert response.json()["result_version"] == 1
        assert response.json()["business_status"] == WorkflowStatus.NEEDS_REVIEW.value
        assert harness.client.get(f"/v1/review-jobs/{job_id}").json()["job_status"] == "succeeded"

    def test_waiting_for_human_is_reported_as_a_distinct_durable_status(self) -> None:
        harness = build()
        accepted = harness.client.post("/v1/review-jobs", json=body()).json()
        job_id, run_id = accepted["job"]["job_id"], accepted["run"]["run_id"]
        attempt = claim(harness, job_id, run_id)
        task_id = uuid4()
        asyncio.run(harness.service.wait_for_human(attempt, (task_id,)))  # type: ignore[arg-type]
        payload = harness.client.get(f"/v1/review-jobs/{job_id}").json()
        assert payload["job_status"] == JobStatus.WAITING_FOR_HUMAN.value
        assert payload["open_task_ids"] == [str(task_id)]
        # It is not a failure, so it carries no problem and consumed no attempt.
        assert payload["problem"] is None
        assert payload["attempt_count"] == 0


class TestCrossPrincipalIsolation:
    def _other_job(self, harness: Harness) -> str:
        return harness.client.post("/v1/review-jobs", json=body()).json()["job"]["job_id"]

    @pytest.mark.parametrize("suffix", ["", "/result"])
    def test_another_principals_job_is_reported_as_absent(self, suffix: str) -> None:
        owner = build()
        job_id = self._other_job(owner)
        intruder = build(principal("reviewer-two"))
        intruder.service.store = owner.store
        response = intruder.client.get(f"/v1/review-jobs/{job_id}{suffix}")
        # 403 would confirm the job exists; only 404 keeps existence private.
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    def test_an_unknown_job_is_indistinguishable_from_a_forbidden_one(self) -> None:
        harness = build()
        known = self._other_job(harness)
        intruder = build(principal("reviewer-two"))
        intruder.service.store = harness.store
        forbidden = intruder.client.get(f"/v1/review-jobs/{known}")
        missing = intruder.client.get(f"/v1/review-jobs/{uuid4()}")
        assert forbidden.status_code == missing.status_code == 404
        assert forbidden.json() == missing.json()

    def test_a_malformed_job_id_is_rejected_without_a_lookup(self) -> None:
        harness = build()
        response = harness.client.get("/v1/review-jobs/not-a-uuid")
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"


class TestCancellation:
    def test_a_queued_job_can_be_cancelled_by_its_owner(self) -> None:
        harness = build()
        job_id = harness.client.post("/v1/review-jobs", json=body()).json()["job"]["job_id"]
        response = harness.client.post(f"/v1/review-jobs/{job_id}/cancel")
        assert response.status_code == 202
        assert response.json()["job_status"] == JobStatus.CANCELLED.value
        assert response.json()["problem"] is not None

    def test_cancelling_a_running_job_asks_it_to_stop_rather_than_killing_it(self) -> None:
        harness = build()
        accepted = harness.client.post("/v1/review-jobs", json=body()).json()
        attempt = claim(harness, accepted["job"]["job_id"], accepted["run"]["run_id"])
        job_id = accepted["job"]["job_id"]
        response = harness.client.post(f"/v1/review-jobs/{job_id}/cancel")
        assert response.json()["job_status"] == JobStatus.RUNNING.value
        # The caller sees the decision it just made, rather than a job that still reads
        # as plainly running until the worker gets around to acknowledging it.
        assert response.json()["cancel_requested"] is True
        assert harness.client.get(f"/v1/review-jobs/{job_id}").json()["cancel_requested"] is True
        state = asyncio.run(harness.service.heartbeat(attempt))  # type: ignore[arg-type]
        assert state.cancel_requested is True

    def test_a_second_cancellation_of_a_terminal_job_conflicts(self) -> None:
        harness = build()
        job_id = harness.client.post("/v1/review-jobs", json=body()).json()["job"]["job_id"]
        harness.client.post(f"/v1/review-jobs/{job_id}/cancel")
        response = harness.client.post(f"/v1/review-jobs/{job_id}/cancel")
        assert response.status_code == 409

    def test_another_principal_cannot_cancel(self) -> None:
        owner = build()
        job_id = owner.client.post("/v1/review-jobs", json=body()).json()["job"]["job_id"]
        intruder = build(principal("reviewer-two"))
        intruder.service.store = owner.store
        assert intruder.client.post(f"/v1/review-jobs/{job_id}/cancel").status_code == 404


class TestUnconfiguredDeployment:
    def test_routes_exist_but_report_an_undeployed_capability(self) -> None:
        client = TestClient(create_app())
        responses = [
            client.post("/v1/review-jobs", json=body()),
            client.get(f"/v1/review-jobs/{uuid4()}"),
            client.get(f"/v1/review-jobs/{uuid4()}/result"),
            client.post(f"/v1/review-jobs/{uuid4()}/cancel"),
        ]
        for response in responses:
            # An undeployed durable plane must not answer as if work were accepted.
            assert response.status_code == 503
            assert response.json()["code"] == "capability_unavailable"

    def test_a_store_without_an_authenticator_is_refused_at_composition(self) -> None:
        with pytest.raises(ValueError, match="principal resolver"):
            create_app(job_service=ReviewJobService(InMemoryJobStore(), InMemoryResultStore()))


class TestContract:
    def test_the_routes_are_published_in_the_openapi_document(self) -> None:
        paths = build().client.get("/openapi.json").json()["paths"]
        assert "/v1/review-jobs" in paths
        assert "/v1/review-jobs/{job_id}" in paths
        assert "/v1/review-jobs/{job_id}/result" in paths
        assert "/v1/review-jobs/{job_id}/cancel" in paths

    def test_no_general_purpose_retry_or_redrive_endpoint_exists(self) -> None:
        # Retry and dead-letter redrive stay operator actions bound to a runbook; an HTTP
        # route able to reschedule arbitrary jobs is the admin surface #29 forbids.
        paths = build().client.get("/openapi.json").json()["paths"]
        assert not [path for path in paths if "retry" in path or "redrive" in path]
        assert not [path for path in paths if "admin" in path]

    def test_the_synchronous_review_route_is_unchanged(self) -> None:
        paths = build().client.get("/openapi.json").json()["paths"]
        assert "/v1/reviews" in paths
        assert set(paths["/v1/reviews"]["post"]["responses"]) == {"200", "422", "500", "503"}

    def test_submission_documents_the_replay_and_acceptance_shapes(self) -> None:
        document = build().client.get("/openapi.json").json()
        responses = document["paths"]["/v1/review-jobs"]["post"]["responses"]
        assert set(responses) >= {"200", "202", "403", "404", "409", "422", "503"}


def test_finish_rejects_an_event_that_is_not_lease_bound() -> None:
    harness = build()
    accepted = harness.client.post("/v1/review-jobs", json=body()).json()
    attempt = claim(harness, accepted["job"]["job_id"], accepted["run"]["run_id"])
    with pytest.raises(ValueError, match="lease-bound"):
        asyncio.run(
            harness.store.finish(
                attempt,  # type: ignore[arg-type]
                event=JobEvent.SCHEDULE_RETRY,
                now=NOW,
                problem=ServiceProblem(code="execution_failed"),  # type: ignore[arg-type]
            )
        )
