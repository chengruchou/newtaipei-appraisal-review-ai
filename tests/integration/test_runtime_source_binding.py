"""Real C2 local storage at job admission; injected owner ports remain offline doubles."""

import asyncio
import runpy
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.aws.runtime_jobs import NoResultAccess
from appraisal_review.adapters.local.document_authority import (
    ConfiguredDocumentAuthorization,
    DocumentGrant,
)
from appraisal_review.adapters.local.job_store import InMemoryJobStore
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.runtime_sources import SnapshotBoundExecution, SnapshotJobService
from appraisal_review.application.runtime_worker import ExecutedReview
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.document_transfer import (
    DocumentOperation,
    ObjectKey,
    RunSourceSnapshot,
)
from appraisal_review.domain.review_contracts import ComparisonContext
from appraisal_review.domain.service_contracts import (
    MaterialRevision,
    ReviewSubmission,
    RuleReference,
)
from appraisal_review.ports.jobs import ClaimedAttempt
from appraisal_review.testing.job_store_contract import principal as contract_principal
from appraisal_review.testing.job_store_contract import submission as contract_submission

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def admitted(tmp_path):
    source = runpy.run_path(str(ROOT / "cloud_tests/extraction_smoke.py"))["synthetic_source"]
    documents, principal, requests = source(tmp_path)
    run = requests[0].run
    stored = documents.storage.read(
        ObjectKey(kind="snapshots", scope=UUID(run.revision.case_id), identity=run.run_id),
        version=None,
        limit=100000,
    )
    snapshot = RunSourceSnapshot.model_validate_json(stored.content)
    material = MaterialRevision(
        reference=run.revision,
        documents=tuple(e.document for e in snapshot.documents),
        rules=(
            RuleReference(
                rule_set_id="synthetic-candidate",
                version="1",
                content_hash="f" * 64,
                context=ComparisonContext(
                    scope="regional",
                    target_id="synthetic-target",
                    comparable_id="synthetic-comparable",
                ),
            ),
        ),
    )
    submission = ReviewSubmission(
        revision=material.reference,
        documents=material.documents,
        idempotency_key="synthetic-runtime-key",
    )
    service = SnapshotJobService(InMemoryJobStore(), NoResultAccess())
    service.bind_sources(documents, AsyncMock(read=AsyncMock(return_value=material)))
    return service, documents, principal, submission


def test_admission_pins_c2_before_job_and_replay_reuses_actual_run(admitted):
    service, documents, principal, submission = admitted

    async def exercise():
        first = await service.submit(principal, submission)
        replay = await service.submit(principal, submission)
        assert first.created and not replay.created
        assert replay.status.current_run == first.status.current_run
        for document in submission.documents:
            loaded = documents.read_snapshot(principal, first.status.current_run, document)
            assert loaded.metadata.reference == document
        assert len(await service.due_dispatches()) == 1

    asyncio.run(exercise())


def test_missing_snapshot_permission_creates_no_job_or_outbox(admitted):
    service, documents, principal, submission = admitted
    documents.authorization.grants = ()

    async def exercise():
        with pytest.raises(ServiceFault) as error:
            await service.submit(principal, submission)
        assert error.value.problem.code == "unauthorized"
        assert await service.due_dispatches() == ()

    asyncio.run(exercise())


@pytest.mark.parametrize("when", ["before", "after"])
def test_worker_rechecks_current_grants_and_sources_around_execution(admitted, when):
    service, documents, principal, submission = admitted

    async def exercise():
        accepted = await service.submit(principal, submission)
        record = await service.store.read_job(job_id=accepted.status.job.job_id)
        principals = AsyncMock(read=AsyncMock(return_value=principal))

        async def execute(*args):
            documents.authorization.grants = ()
            return ExecutedReview(persisted_task_ids=(uuid4(),))

        inner = AsyncMock(execute=AsyncMock(side_effect=execute))
        loader = AsyncMock(read_submission=AsyncMock(return_value=submission))
        bound = SnapshotBoundExecution(inner, documents, loader, principals)
        attempt = ClaimedAttempt(
            record.job_id, record.current_run.run_id, uuid4(), uuid4(), 1, 0, 100
        )
        if when == "before":
            principals.read.return_value = replace(principal, case_ids=frozenset())
        with pytest.raises(ServiceFault):
            await bound.execute(record, attempt)
        assert inner.execute.await_count == (0 if when == "before" else 1)

    asyncio.run(exercise())


@pytest.mark.parametrize("blocked_document", [0, 1])
def test_snapshot_only_permission_cannot_commit_a_job_or_outbox(monkeypatch, blocked_document):
    """Independent of the extraction smoke helper and any real document storage."""
    actor_id, case_id = uuid4(), uuid4()
    principal = contract_principal(actor_id=str(actor_id), case_id=str(case_id))
    submission = contract_submission(case_id=str(case_id), revision_id=str(uuid4()))
    material = MaterialRevision(
        reference=submission.revision,
        documents=submission.documents,
        rules=(
            RuleReference(
                rule_set_id="synthetic",
                version="1",
                content_hash="f" * 64,
                context=ComparisonContext(
                    scope="regional", target_id="target", comparable_id="comparable"
                ),
            ),
        ),
    )
    authorization = ConfiguredDocumentAuthorization(
        (
            DocumentGrant(
                actor_id=actor_id,
                case_id=case_id,
                purposes=frozenset(reference.purpose for reference in submission.documents),
                operations=frozenset({DocumentOperation.SNAPSHOT}),
            ),
            DocumentGrant(
                actor_id=actor_id,
                case_id=case_id,
                purposes=frozenset({submission.documents[1 - blocked_document].purpose}),
                operations=frozenset({DocumentOperation.READ}),
            ),
        )
    )

    def snapshot(caller, run, revision):
        for reference in revision.documents:
            authorization.require(
                caller, reference.case_id, reference.purpose, DocumentOperation.SNAPSHOT
            )

    def read(caller, run, reference):
        authorization.require(caller, reference.case_id, reference.purpose, DocumentOperation.READ)

    documents = Mock(spec=DocumentTransferService)
    documents.create_snapshot.side_effect = snapshot
    documents.read_snapshot.side_effect = read
    jobs = InMemoryJobStore()
    create_job = AsyncMock(wraps=jobs.create_job)
    monkeypatch.setattr(jobs, "create_job", create_job)
    service = SnapshotJobService(jobs, NoResultAccess())
    service.bind_sources(documents, AsyncMock(read=AsyncMock(return_value=material)))

    async def exercise():
        with pytest.raises(ServiceFault) as caught:
            await service.submit(principal, submission)
        assert caught.value.problem.code == "unauthorized"
        documents.create_snapshot.assert_called_once()
        assert documents.read_snapshot.call_count == blocked_document + 1
        create_job.assert_not_awaited()
        assert await service.due_dispatches() == ()

    asyncio.run(exercise())
