"""Versioned, bounded control-plane rows; never serialize a submission or result body."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, TypeVar
from uuid import UUID

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from pydantic import BaseModel, ConfigDict, Field

from appraisal_review.domain.factor_models import ArtifactStatus, WorkflowStatus
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    ExecutionStatus,
    OpaqueID,
    RevisionReference,
    RunReference,
    ServiceProblem,
)
from appraisal_review.ports.jobs import DispatchRecord, JobRecord, ResultReference

Counter = Annotated[int, Field(ge=0, le=2**63 - 1)]
DigestValue = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
# Keep each item and every transaction bounded well below service limits.
MAX_DOCUMENTS = 64
MAX_TASKS = 256
MAX_ARTIFACTS = 256
MAX_ITEM_BYTES = 128 * 1024


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    storage_schema: Literal["job-store-v1"] = "job-store-v1"
    version: int = Field(default=1, ge=1, le=2**63 - 1)

    def key(self) -> dict[str, str]:
        raise NotImplementedError

    def recovery(self) -> dict[str, str | int]:
        return {}


class JobRow(Row):
    kind: Literal["job"] = "job"
    job_id: UUID
    case_id: OpaqueID
    principal_id: OpaqueID
    status: JobStatus
    current_run_id: UUID
    attempt_count: Counter = 0
    lease_takeover_count: Counter = 0
    cancel_requested: bool = False
    open_task_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_TASKS)
    problem: ServiceProblem | None = None
    outbox_seq: Counter = 0
    created_at: Counter
    updated_at: Counter

    def key(self) -> dict[str, str]:
        return {"pk": f"JOB#{self.job_id}", "sk": "META"}

    def recovery(self) -> dict[str, str | int]:
        if self.status == JobStatus.RETRYABLE_FAILED:
            return {"recovery_pk": "RETRY", "recovery_at": self.updated_at}
        return {}

    def record(self, run: RunRow) -> JobRecord:
        return JobRecord(
            job_id=self.job_id,
            case_id=self.case_id,
            principal_id=self.principal_id,
            status=self.status,
            current_run=RunReference(
                run_id=run.run_id,
                revision=run.revision,
                attempt_id=run.attempt_id if self.status == JobStatus.RUNNING else None,
            ),
            attempt_count=self.attempt_count,
            lease_takeover_count=self.lease_takeover_count,
            result_version=run.result_version,
            cancel_requested=self.cancel_requested,
            open_task_ids=self.open_task_ids,
            problem=self.problem,
        )


class RunRow(Row):
    kind: Literal["run"] = "run"
    job_id: UUID
    run_id: UUID
    revision: RevisionReference
    documents: tuple[DocumentReference, ...] = Field(min_length=2, max_length=MAX_DOCUMENTS)
    payload_digest: DigestValue
    status: JobStatus = JobStatus.QUEUED
    fencing_token: Counter = 0
    result_version: Counter = 0
    lease_owner: UUID | None = None
    lease_expires_at: Counter | None = None
    attempt_id: UUID | None = None

    def key(self) -> dict[str, str]:
        return {"pk": f"RUN#{self.run_id}", "sk": "META"}

    def recovery(self) -> dict[str, str | int]:
        if self.lease_expires_at is not None:
            return {"recovery_pk": "LEASE", "recovery_at": self.lease_expires_at}
        return {}


class OutboxRow(Row):
    kind: Literal["outbox"] = "outbox"
    job_id: UUID
    run_id: UUID
    outbox_seq: Counter
    dispatch_token: UUID
    available_at: Counter
    dispatch_attempts: Counter = 0
    dispatch_state: Literal["pending", "sent", "abandoned"] = "pending"

    def key(self) -> dict[str, str]:
        return {"pk": f"JOB#{self.job_id}", "sk": f"OUTBOX#{self.outbox_seq:020d}"}

    def recovery(self) -> dict[str, str | int]:
        if self.dispatch_state == "pending":
            return {"recovery_pk": "PENDING", "recovery_at": self.available_at}
        return {}

    def record(self) -> DispatchRecord:
        return DispatchRecord(
            job_id=self.job_id,
            run_id=self.run_id,
            outbox_seq=self.outbox_seq,
            dispatch_token=self.dispatch_token,
            available_at=self.available_at,
            dispatch_attempts=self.dispatch_attempts,
        )


class AttemptRow(Row):
    kind: Literal["attempt"] = "attempt"
    job_id: UUID
    run_id: UUID
    attempt_id: UUID
    owner: UUID
    fencing_token: Counter
    claimed_at: Counter
    closed_at: Counter | None = None
    outcome: Literal[
        "running",
        "publish_result",
        "needs_human",
        "retryable_error",
        "permanent_error",
        "cancel_acknowledged",
        "lease_expired",
    ] = "running"

    def key(self) -> dict[str, str]:
        return {"pk": f"RUN#{self.run_id}", "sk": f"ATTEMPT#{self.attempt_id}"}


class IdempotencyRow(Row):
    kind: Literal["idempotency"] = "idempotency"
    namespace_digest: DigestValue
    principal_id: OpaqueID
    payload_digest: DigestValue
    job_id: UUID
    run_id: UUID

    def key(self) -> dict[str, str]:
        return {"pk": f"IDEM#{self.namespace_digest}", "sk": "META"}


class ResultRow(Row):
    kind: Literal["result"] = "result"
    run_id: UUID
    result_version: int = Field(ge=1, le=2**63 - 1)
    fencing_token: int = Field(ge=1, le=2**63 - 1)
    execution_status: ExecutionStatus
    business_status: WorkflowStatus | None
    artifact_status: ArtifactStatus
    result_digest: DigestValue
    artifact_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_ARTIFACTS)
    finding_count: Counter = 0

    def key(self) -> dict[str, str]:
        return {"pk": f"RUN#{self.run_id}", "sk": f"RESULT#{self.result_version:020d}"}

    def record(self) -> ResultReference:
        return ResultReference(
            run_id=self.run_id,
            result_version=self.result_version,
            fencing_token=self.fencing_token,
            execution_status=self.execution_status,
            business_status=self.business_status,
            artifact_status=self.artifact_status,
            result_digest=self.result_digest,
            artifact_ids=self.artifact_ids,
            finding_count=self.finding_count,
        )


def idempotency_digest(principal_id: str, key: str) -> str:
    """Length-delimited namespace: no raw caller key or ambiguous delimiter joins."""
    return hashlib.sha256(
        json.dumps([principal_id, key], ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def marshal(value: dict[str, Any]) -> dict[str, Any]:
    serializer = TypeSerializer()
    return {name: serializer.serialize(item) for name, item in value.items()}


def encode(row: Row) -> dict[str, Any]:
    # Revalidate copies so even unsafe model_copy or mutated nested models cannot smuggle
    # unknown attributes, diagnostics or oversized lists through this persistence boundary.
    checked = type(row).model_validate(row.model_dump(mode="json", warnings="error"))
    value = {**checked.model_dump(mode="json", exclude_none=True), **row.key(), **row.recovery()}
    if len(json.dumps(value, ensure_ascii=True).encode()) > MAX_ITEM_BYTES:
        raise ValueError("Control-plane item exceeds its size bound")
    return marshal(value)


R = TypeVar("R", bound=Row)


def decode(value: dict[str, Any], row_type: type[R]) -> R:
    deserializer = TypeDeserializer()
    fields = {name: deserializer.deserialize(item) for name, item in value.items()}
    key = {name: fields.pop(name) for name in ("pk", "sk")}
    recovery = {name: fields.pop(name) for name in ("recovery_pk", "recovery_at") if name in fields}
    row = row_type.model_validate(fields)
    if key != row.key() or recovery != row.recovery():
        raise ValueError("Control-plane item identity or index is inconsistent")
    return row
