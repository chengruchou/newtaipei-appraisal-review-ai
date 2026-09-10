"""Local privacy canary for the job control plane.

A synthetic marker is planted in the one place a result legitimately carries document
text — a finding's citation excerpt — and then every control-plane surface is scanned for
it. The control plane must hold only opaque identifiers, versions, digests, statuses,
timestamps and counts.

This is the local half of #29's canary requirement. The cloud half, which scans DynamoDB,
SQS, the dead-letter queue and CloudWatch, belongs to #31; running it here first means the
boundary is defended before anything reaches an AWS account.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.job_store import InMemoryJobStore, InMemoryResultStore
from appraisal_review.application.job_state import JobPolicy
from appraisal_review.application.review_jobs import ReviewJobService, status_view
from appraisal_review.domain.document_models import Box, SourceCitation
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.review_contracts import ReviewFinding
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    RunReference,
    ServiceErrorCode,
    ServiceResult,
)
from appraisal_review.testing.job_store_contract import NOW, digest, principal, submission

# Deliberately synthetic and obviously artificial: a realistic-looking identifier used as
# a canary would itself be a privacy problem in test fixtures and CI logs.
CANARY = "CANARY-7Q2X-SYNTHETIC-MARKER"


def canary_result(run_id: UUID, revision: Any) -> ServiceResult:
    """A result whose finding legitimately quotes document text."""
    citation = SourceCitation(
        document_id="doc-criteria",
        content_hash=digest("b"),
        version="1",
        page=1,
        region_id="region-1",
        bbox=Box((0.0, 0.0, 1.0, 1.0)),
        excerpt=f"Observed value near {CANARY}",
    )
    return ServiceResult(
        run=RunReference(run_id=run_id, revision=revision),
        result_version=1,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=WorkflowStatus.NEEDS_REVIEW,
        findings=(
            ReviewFinding(
                id="canary-finding",
                kind="confirmation",
                status="needs_review",
                evidence=[citation],
                trace=f"Synthetic trace quoting {CANARY}",
            ),
        ),
    )


def serialize(value: object) -> str:
    return json.dumps(value, default=repr, sort_keys=True)


@pytest.fixture()
def planted() -> tuple[InMemoryJobStore, InMemoryResultStore, ReviewJobService, UUID]:
    store = InMemoryJobStore()
    results = InMemoryResultStore()
    service = ReviewJobService(
        store, results, policy=JobPolicy(), clock=lambda: NOW, jitter=lambda bound: 0
    )
    payload = submission()
    outcome = asyncio.run(service.submit(principal(), payload))
    assert outcome.acceptance is not None
    run_id = outcome.acceptance.run.run_id
    attempt = asyncio.run(
        service.claim(job_id=outcome.acceptance.job.job_id, run_id=run_id, owner=uuid4())
    )
    asyncio.run(service.publish(attempt, canary_result(run_id, payload.revision)))
    return store, results, service, outcome.acceptance.job.job_id


def test_the_canary_is_detectable_where_it_legitimately_lives(
    planted: tuple[InMemoryJobStore, InMemoryResultStore, ReviewJobService, UUID],
) -> None:
    _, results, _, _ = planted
    body = asyncio.run(results.get(run_id=_first_run(planted), result_version=1))
    # Without this the whole file could pass by never planting anything.
    assert body is not None
    assert CANARY in body.model_dump_json()


def _first_run(
    planted: tuple[InMemoryJobStore, InMemoryResultStore, ReviewJobService, UUID],
) -> UUID:
    store, _, _, job_id = planted
    record = asyncio.run(store.read_job(job_id=job_id))
    assert record is not None
    return record.current_run.run_id


def test_the_control_plane_never_stores_document_text(
    planted: tuple[InMemoryJobStore, InMemoryResultStore, ReviewJobService, UUID],
) -> None:
    store, _, _, _ = planted
    # Jobs, runs, attempts, outbox entries, idempotency records and result references.
    assert CANARY not in serialize(store.stored_items())


def test_the_dispatch_payload_carries_only_opaque_identifiers(
    planted: tuple[InMemoryJobStore, InMemoryResultStore, ReviewJobService, UUID],
) -> None:
    store, _, service, _ = planted
    pending = asyncio.run(service.due_dispatches(limit=10))
    # A queue message is read by CloudWatch, dead-letter tooling and redrive operators,
    # so it carries references and never the submitted payload.
    for record in pending:
        fields = serialize(record)
        assert CANARY not in fields
        for leaked in ("excerpt", "raw_text", "s3://", "content_hash", "document_id"):
            assert leaked not in fields
    assert asyncio.run(store.read_job(job_id=pending[0].job_id)) is not None


def test_the_status_projection_never_carries_findings(
    planted: tuple[InMemoryJobStore, InMemoryResultStore, ReviewJobService, UUID],
) -> None:
    store, _, _, job_id = planted
    record = asyncio.run(store.read_job(job_id=job_id))
    assert record is not None
    view = status_view(record)
    assert CANARY not in view.model_dump_json()
    assert "findings" not in view.model_dump()


def test_a_committed_reference_keeps_only_a_digest_and_a_count(
    planted: tuple[InMemoryJobStore, InMemoryResultStore, ReviewJobService, UUID],
) -> None:
    store, results, _, _ = planted
    run_id = _first_run(planted)
    reference = asyncio.run(store.read_result_reference(run_id=run_id, result_version=1))
    body = asyncio.run(results.get(run_id=run_id, result_version=1))
    assert reference is not None and body is not None
    assert reference.finding_count == 1
    assert CANARY not in serialize(reference)
    # The reference must still pin the exact body, or a swapped result could be served.
    assert len(reference.result_digest) == 64


def test_every_public_problem_is_a_fixed_code_and_message() -> None:
    from appraisal_review.domain.service_contracts import ServiceProblem

    for code in ServiceErrorCode:
        problem = ServiceProblem(code=code)
        assert problem.message == "Service operation could not be completed."
        assert set(problem.model_dump()) == {"schema_version", "code", "message"}
