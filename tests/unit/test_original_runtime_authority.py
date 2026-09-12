"""Synthetic local authority unit probes; no real case confirmation or approval."""

import asyncio
import time
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.case_preparation import prepare_case
from appraisal_review.adapters.local.original_workbench import (
    LocalCandidateExecution,
    prepare_workbench,
)
from appraisal_review.adapters.local.workflow_runtime import SQLiteExecutionAuthority
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import (
    AcceptedResponse,
    HumanResponse,
    ServiceErrorCode,
)
from tests.unit import test_case_preparation as preparation_tests


async def runtime(tmp_path):
    inputs = preparation_tests.inputs.__wrapped__(tmp_path)
    candidate = prepare_case(**inputs).material
    material_path = tmp_path / "material.json"
    manifest_path = tmp_path / "manifest.json"
    material_path.write_text(candidate.model_dump_json())
    manifest_path.write_text(inputs["manifest"].model_dump_json())
    app = await prepare_workbench(
        manifest_path=manifest_path,
        material_path=material_path,
        data_directory=tmp_path / "runtime",
        port=18668,
    )
    store = app.state.material_catalog.store
    principal = app.state.local_original_grant.principal
    job_id = app.state.configured_workbench_jobs()[0]
    record = await store.read_job(job_id=job_id)
    snapshot = await app.state.material_catalog.snapshot(principal, record.current_run.revision)
    return app, store, principal, record, snapshot


def test_response_requires_used_sources_and_maps_revocation_to_typed_error(tmp_path):
    async def scenario():
        app, store, principal, record, snapshot = await runtime(tmp_path)
        grant = app.state.local_original_grant
        assert "brief" not in grant.purposes
        task = LocalCandidateExecution._side_tasks(
            record, snapshot.material.facts.pairs[0], "target", "unit-finding"
        )[0]
        command = HumanResponse(
            task_id=task.task_id,
            expected_version=task.version,
            revision=task.run.revision,
            side_digest=task.side.input_digest,
            idempotency_key="unit-check",
            action="confirm",
        )
        accepted = AcceptedResponse(actor=principal.actor, command=command)
        # An absent purpose is not a requirement. No response or receipt is committed.
        store.response_authority(accepted, task)
        grant.purposes = grant.purposes - {"forms"}
        with pytest.raises(ServiceFault) as failure:
            store.response_authority(accepted, task)
        assert failure.value.problem.code == ServiceErrorCode.UNAUTHORIZED
        assert (
            await store.read_receipt(principal_id=principal.actor.actor_id, key="unit-check")
            is None
        )

    asyncio.run(scenario())


def test_purpose_revoked_after_bytes_read_blocks_fenced_task_registration(tmp_path):
    async def scenario():
        app, store, principal, record, snapshot = await runtime(tmp_path)
        now = int(time.time())
        attempt = await store.claim(
            job_id=record.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=120,
            now=now,
        )
        record = await store.read_job(job_id=record.job_id)
        documents = app.state.local_original_documents
        documents.create_snapshot(principal, record.current_run, snapshot.revision)
        directory = app.state.local_original_directory
        original_read = directory.read
        calls = 0

        async def revoke_after_last_source_read(principal_id, case_id):
            nonlocal calls
            current = await original_read(principal_id, case_id)
            calls += 1
            if calls == 2:
                app.state.local_original_grant.purposes -= {"forms"}
            return current

        directory.read = revoke_after_last_source_read
        guard = SQLiteExecutionAuthority(store, documents, directory)
        tasks = tuple(
            LocalCandidateExecution._side_tasks(
                record, snapshot.material.facts.pairs[0], "target", "unit-finding"
            )
        )
        assert tasks
        with pytest.raises(ServiceFault) as failure:
            await guard.register(record, attempt, snapshot, tasks)
        assert failure.value.problem.code == ServiceErrorCode.UNAUTHORIZED
        assert calls == 2
        assert not await store.list_tasks(job_id=record.job_id)

    asyncio.run(scenario())
