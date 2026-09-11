"""DynamoDB JobStore with separate entities, transactional CAS and sparse recovery.

Inject a low-level boto3 client. No session, credential lookup, resource creation or
in-process state is owned here. Blocking SDK calls run outside the async event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from typing import Any, Literal, Protocol, TypeVar
from uuid import UUID, uuid4

from boto3.dynamodb.conditions import Attr, ConditionBase, ConditionExpressionBuilder, Key
from botocore.exceptions import BotoCoreError, ClientError, ParamValidationError
from pydantic import ValidationError

from appraisal_review.adapters.aws.job_serialization import (
    AttemptRow,
    IdempotencyRow,
    JobRow,
    OutboxRow,
    ResultRow,
    Row,
    RunRow,
    decode,
    encode,
    idempotency_digest,
    marshal,
)
from appraisal_review.application.job_state import (
    JobEvent,
    JobFacts,
    JobPolicy,
    initial_transition,
    next_state,
)
from appraisal_review.application.service_guards import (
    Principal,
    ServiceFault,
    submission_digest,
)
from appraisal_review.domain.job_contracts import TERMINAL_STATUSES, JobStatus
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    Permission,
    ReviewSubmission,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
)
from appraisal_review.ports.jobs import (
    ClaimedAttempt,
    ConditionFailed,
    DispatchRecord,
    ExpiredLease,
    HeartbeatState,
    JobRecord,
    ResultReference,
)

FINISH_EVENTS = frozenset(
    {
        JobEvent.PUBLISH_RESULT,
        JobEvent.NEEDS_HUMAN,
        JobEvent.RETRYABLE_ERROR,
        JobEvent.PERMANENT_ERROR,
        JobEvent.CANCEL_ACKNOWLEDGED,
    }
)
ErrorReason = Literal["throttled", "unavailable", "denied", "invalid", "corrupt"]


class DynamoDBJobStoreError(ServiceFault):
    """Finite internal reason; the existing public service problem remains sanitized."""

    def __init__(self, reason: ErrorReason, *, retryable: bool = False) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(ServiceErrorCode.CAPABILITY)


class DynamoDBJobClient(Protocol):
    def get_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def transact_get_items(self, **kwargs: Any) -> dict[str, Any]: ...
    def transact_write_items(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_paginator(self, operation_name: str) -> Any: ...


R = TypeVar("R", bound=Row)
Change = tuple[Row, Row | None, ConditionBase | None]


def _expression(condition: ConditionBase, *, query: bool = False) -> dict[str, Any]:
    built = ConditionExpressionBuilder().build_expression(condition, is_key_condition=query)
    result = {
        "KeyConditionExpression" if query else "ConditionExpression": built.condition_expression,
        "ExpressionAttributeNames": built.attribute_name_placeholders,
    }
    if built.attribute_value_placeholders:
        result["ExpressionAttributeValues"] = marshal(built.attribute_value_placeholders)
    return result


def _storage_error(error: Exception) -> Exception:
    """Adapter boundary: neither SDK text nor response items escape into diagnostics."""
    if isinstance(error, ParamValidationError):
        return DynamoDBJobStoreError("invalid")
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return ConditionFailed("Stored state changed")
        if code == "TransactionCanceledException":
            reasons = {entry.get("Code") for entry in error.response.get("CancellationReasons", [])}
            if reasons & {"ProvisionedThroughputExceeded", "ThrottlingError"}:
                return DynamoDBJobStoreError("throttled", retryable=True)
            if "TransactionConflict" in reasons:
                return DynamoDBJobStoreError("unavailable", retryable=True)
            if reasons & {"ValidationError", "ItemCollectionSizeLimitExceeded"}:
                return DynamoDBJobStoreError("invalid")
            if "ConditionalCheckFailed" in reasons:
                return ConditionFailed("Stored state changed")
            return DynamoDBJobStoreError("unavailable", retryable=True)
        if code in {
            "ProvisionedThroughputExceededException",
            "ThrottlingException",
            "RequestLimitExceeded",
        }:
            return DynamoDBJobStoreError("throttled", retryable=True)
        if code in {
            "AccessDeniedException",
            "UnrecognizedClientException",
            "ExpiredTokenException",
        }:
            return DynamoDBJobStoreError("denied")
        if code in {"ValidationException", "ResourceNotFoundException"}:
            return DynamoDBJobStoreError("invalid")
    return DynamoDBJobStoreError("unavailable", retryable=True)


def _facts(job: JobRow) -> JobFacts:
    return JobFacts(
        status=job.status,
        attempt_count=job.attempt_count,
        lease_takeover_count=job.lease_takeover_count,
        cancel_requested=job.cancel_requested,
        has_open_tasks=bool(job.open_task_ids),
    )


def _release(run: RunRow) -> RunRow:
    return run.model_copy(
        update={"lease_owner": None, "lease_expires_at": None, "attempt_id": None}
    )


class DynamoDBJobStore:
    """A configured table and sparse GSI; all authority rests in database conditions.

    The injected client must have bounded SDK retries/timeouts. Transaction requests use
    one random idempotency token per operation, reused automatically by SDK retries.
    A condition failure is never retried here. Callers re-read and decide a fresh action.
    """

    def __init__(
        self,
        client: DynamoDBJobClient,
        *,
        table_name: str,
        index_name: str = "job-recovery",
        policy: JobPolicy | None = None,
    ) -> None:
        if not table_name or not index_name:
            raise ValueError("A job table and recovery index are required")
        self.client = client
        self.table_name = table_name
        self.index_name = index_name
        self.policy = policy or JobPolicy()

    async def _call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response: dict[str, Any] = await asyncio.to_thread(
                getattr(self.client, operation), **kwargs
            )
            return response
        except (ClientError, BotoCoreError) as error:
            raise _storage_error(error) from None

    @staticmethod
    def _decode(item: dict[str, Any], row_type: type[R]) -> R:
        try:
            return decode(item, row_type)
        except (ValueError, TypeError, KeyError):
            raise DynamoDBJobStoreError("corrupt") from None

    async def _get(self, key: dict[str, str], row_type: type[R]) -> R | None:
        response = await self._call(
            "get_item",
            TableName=self.table_name,
            Key=marshal(key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        row = self._decode(item, row_type)
        if row.key() != key:
            raise DynamoDBJobStoreError("corrupt")
        return row

    async def _snapshot(self, job_id: UUID) -> tuple[JobRow, RunRow] | None:
        key = {"pk": f"JOB#{job_id}", "sk": "META"}
        # Locate the current run, then read both in one serializable transaction. A
        # concurrent human resume can change the pointer; retry only this read snapshot.
        for _ in range(3):
            first = await self._get(key, JobRow)
            if first is None:
                return None
            run_key = {"pk": f"RUN#{first.current_run_id}", "sk": "META"}
            response = await self._call(
                "transact_get_items",
                TransactItems=[
                    {"Get": {"TableName": self.table_name, "Key": marshal(k)}}
                    for k in (key, run_key)
                ],
            )
            try:
                job = self._decode(response["Responses"][0]["Item"], JobRow)
                run = self._decode(response["Responses"][1]["Item"], RunRow)
            except (KeyError, IndexError, TypeError):
                raise DynamoDBJobStoreError("corrupt") from None
            if job.current_run_id != first.current_run_id:
                continue
            if (
                job.key() != key
                or run.key() != run_key
                or run.job_id != job_id
                or job.status != run.status
                or run.revision.case_id != job.case_id
            ):
                raise DynamoDBJobStoreError("corrupt")
            return job, run
        raise ConditionFailed("Current run changed during read")

    async def _required(self, job_id: UUID) -> tuple[JobRow, RunRow]:
        snapshot = await self._snapshot(job_id)
        if snapshot is None:
            raise ConditionFailed("Unknown job")
        return snapshot

    async def _write(self, changes: list[Change]) -> None:
        transactions = []
        try:
            for row, previous, extra in changes:
                condition: ConditionBase
                if previous is None:
                    condition = Attr("pk").not_exists()
                else:
                    if previous.key() != row.key():
                        raise ValueError("A row identity is immutable")
                    condition = Attr("version").eq(previous.version)
                    row = row.model_copy(update={"version": previous.version + 1})
                if extra is not None:
                    condition &= extra
                transactions.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": encode(row),
                            **_expression(condition),
                        }
                    }
                )
        except (ValidationError, ValueError, TypeError):
            raise ServiceFault(ServiceErrorCode.VALIDATION) from None
        await self._call(
            "transact_write_items",
            TransactItems=transactions,
            ClientRequestToken=str(uuid4()),
        )

    async def read_job(self, *, job_id: UUID) -> JobRecord | None:
        snapshot = await self._snapshot(job_id)
        return None if snapshot is None else snapshot[0].record(snapshot[1])

    async def read_result_reference(
        self,
        *,
        run_id: UUID,
        result_version: int,
    ) -> ResultReference | None:
        row = await self._get(
            {"pk": f"RUN#{run_id}", "sk": f"RESULT#{result_version:020d}"},
            ResultRow,
        )
        return None if row is None else row.record()

    async def read_submission(self, *, run_id: UUID) -> ReviewSubmission | None:
        """Load a run's pinned metadata for a worker that already holds execution authority.

        The original idempotency key is intentionally not recoverable. An internal opaque
        key replaces it; canonical submission identity excludes that key. This read does
        not grant authorization or replace the document service's current-source checks.
        """
        run = await self._get({"pk": f"RUN#{run_id}", "sk": "META"}, RunRow)
        if run is None:
            return None
        try:
            submission = ReviewSubmission(
                revision=run.revision,
                documents=run.documents,
                idempotency_key=f"run-{run.run_id}",
            )
        except ValidationError:
            raise DynamoDBJobStoreError("corrupt") from None
        if submission_digest(submission) != run.payload_digest:
            raise DynamoDBJobStoreError("corrupt")
        return submission

    async def _replay(
        self,
        key: dict[str, str],
        principal: Principal,
        digest: str,
    ) -> JobRecord | None:
        stored = await self._get(key, IdempotencyRow)
        if stored is None:
            return None
        if stored.principal_id != principal.actor.actor_id:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        if stored.payload_digest != digest:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        record = await self.read_job(job_id=stored.job_id)
        if record is None or record.principal_id != stored.principal_id:
            raise DynamoDBJobStoreError("corrupt")
        return record

    @staticmethod
    def _enqueue(job: JobRow, run_id: UUID, available_at: int) -> tuple[JobRow, OutboxRow]:
        job = job.model_copy(update={"outbox_seq": job.outbox_seq + 1})
        try:
            outbox = OutboxRow(
                job_id=job.job_id,
                run_id=run_id,
                outbox_seq=job.outbox_seq,
                dispatch_token=uuid4(),
                available_at=available_at,
            )
        except ValidationError:
            raise ServiceFault(ServiceErrorCode.VALIDATION) from None
        return job, outbox

    async def create_job(
        self,
        principal: Principal,
        submission: ReviewSubmission,
        *,
        job_id: UUID,
        run_id: UUID,
        now: int,
    ) -> tuple[JobRecord, bool]:
        try:
            submission = ReviewSubmission.model_validate_json(submission.model_dump_json())
            principal.require(submission.revision.case_id, Permission.REVIEW)
            digest = submission_digest(submission)
            idem = IdempotencyRow(
                namespace_digest=idempotency_digest(
                    principal.actor.actor_id, submission.idempotency_key
                ),
                principal_id=principal.actor.actor_id,
                payload_digest=digest,
                job_id=job_id,
                run_id=run_id,
            )
            job = JobRow(
                job_id=job_id,
                case_id=submission.revision.case_id,
                principal_id=principal.actor.actor_id,
                status=initial_transition().status,
                current_run_id=run_id,
                created_at=now,
                updated_at=now,
            )
            run = RunRow(
                job_id=job_id,
                run_id=run_id,
                revision=submission.revision,
                documents=submission.documents,
                payload_digest=digest,
            )
        except (ValidationError, ValueError, TypeError):
            raise ServiceFault(ServiceErrorCode.VALIDATION) from None
        existing = await self._replay(idem.key(), principal, digest)
        if existing is not None:
            return existing, False
        job, outbox = self._enqueue(job, run_id, now)
        try:
            await self._write([(row, None, None) for row in (job, run, idem, outbox)])
        except (ConditionFailed, DynamoDBJobStoreError):
            # A racing admission or lost response may already have committed all four
            # rows. Strong replay resolves it without another write or a second outbox.
            existing = await self._replay(idem.key(), principal, digest)
            if existing is not None:
                return existing, False
            raise
        return job.record(run), True

    async def claim(
        self,
        *,
        job_id: UUID,
        run_id: UUID,
        owner: UUID,
        lease_seconds: int,
        now: int,
    ) -> ClaimedAttempt:
        if lease_seconds < 1:
            raise ValueError("A lease must last at least one second")
        job, run = await self._required(job_id)
        if run.run_id != run_id or (
            run.lease_expires_at is not None and run.lease_expires_at > now
        ):
            raise ConditionFailed("Unknown, superseded or leased run")
        try:
            transition = next_state(_facts(job), JobEvent.CLAIM, policy=self.policy)
        except ServiceFault:
            raise ConditionFailed("Run is not claimable") from None
        attempt = AttemptRow(
            job_id=job_id,
            run_id=run_id,
            attempt_id=uuid4(),
            owner=owner,
            fencing_token=run.fencing_token + 1,
            claimed_at=now,
        )
        current_job = job.model_copy(update={"status": transition.status, "updated_at": now})
        current_run = run.model_copy(
            update={
                "status": transition.status,
                "fencing_token": attempt.fencing_token,
                "lease_owner": owner,
                "lease_expires_at": now + lease_seconds,
                "attempt_id": attempt.attempt_id,
            }
        )
        await self._write(
            [
                (current_job, job, Attr("current_run_id").eq(str(run_id))),
                (
                    current_run,
                    run,
                    Attr("lease_expires_at").not_exists() | Attr("lease_expires_at").lte(now),
                ),
                (attempt, None, None),
            ]
        )
        return ClaimedAttempt(
            job_id=job_id,
            run_id=run_id,
            attempt_id=attempt.attempt_id,
            owner=owner,
            fencing_token=attempt.fencing_token,
            expected_result_version=run.result_version,
            lease_expires_at=now + lease_seconds,
        )

    async def _authority(self, attempt: ClaimedAttempt, now: int) -> tuple[JobRow, RunRow]:
        job, run = await self._required(attempt.job_id)
        if (
            run.run_id != attempt.run_id
            or run.lease_owner != attempt.owner
            or run.fencing_token != attempt.fencing_token
            or run.attempt_id != attempt.attempt_id
            or job.status != JobStatus.RUNNING
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
            or run.result_version != attempt.expected_result_version
        ):
            raise ConditionFailed("Attempt no longer holds a valid lease")
        return job, run

    @staticmethod
    def _lease_condition(attempt: ClaimedAttempt, now: int) -> ConditionBase:
        return (
            Attr("lease_owner").eq(str(attempt.owner))
            & Attr("fencing_token").eq(attempt.fencing_token)
            & Attr("attempt_id").eq(str(attempt.attempt_id))
            & Attr("result_version").eq(attempt.expected_result_version)
            & Attr("lease_expires_at").gt(now)
        )

    async def heartbeat(
        self,
        attempt: ClaimedAttempt,
        *,
        lease_seconds: int,
        now: int,
    ) -> HeartbeatState:
        if lease_seconds < 1:
            raise ValueError("A lease must last at least one second")
        job, run = await self._authority(attempt, now)
        next_state(_facts(job), JobEvent.HEARTBEAT, policy=self.policy)
        deadline = max(run.lease_expires_at or 0, now + lease_seconds)
        await self._write(
            [
                (job.model_copy(update={"updated_at": now}), job, None),
                (
                    run.model_copy(update={"lease_expires_at": deadline}),
                    run,
                    self._lease_condition(attempt, now),
                ),
            ]
        )
        return HeartbeatState(lease_expires_at=deadline, cancel_requested=job.cancel_requested)

    async def _close_attempt(self, run: RunRow, event: JobEvent, now: int) -> Change:
        stored = await self._get(
            {"pk": f"RUN#{run.run_id}", "sk": f"ATTEMPT#{run.attempt_id}"},
            AttemptRow,
        )
        if stored is None or (
            stored.job_id != run.job_id
            or stored.owner != run.lease_owner
            or stored.fencing_token != run.fencing_token
            or stored.outcome != "running"
        ):
            raise DynamoDBJobStoreError("corrupt")
        return stored.model_copy(update={"outcome": event.value, "closed_at": now}), stored, None

    async def finish(
        self,
        attempt: ClaimedAttempt,
        *,
        event: JobEvent,
        now: int,
        problem: ServiceProblem | None = None,
        open_task_ids: tuple[UUID, ...] = (),
        result: ResultReference | None = None,
        available_at: int | None = None,
    ) -> JobRecord:
        del available_at  # The port schedules retry independently for crash reconciliation.
        if event not in FINISH_EVENTS:
            raise ValueError("finish requires a lease-bound finish event")
        job, run = await self._authority(attempt, now)
        transition = next_state(
            replace(_facts(job), has_open_tasks=bool(open_task_ids)),
            event,
            policy=self.policy,
        )
        changes: list[Change] = []
        current_run = run.model_copy(update={"status": transition.status})
        if event == JobEvent.PUBLISH_RESULT:
            if result is None:
                raise ValueError("Publication requires a result reference")
            if (
                result.run_id != run.run_id
                or result.fencing_token != attempt.fencing_token
                or result.result_version != attempt.expected_result_version + 1
            ):
                raise ConditionFailed("Stale publication")
            if result.execution_status != ExecutionStatus.SUCCEEDED:
                raise ValueError("Publication requires successful execution")
            try:
                reference = ResultRow.model_validate(asdict(result))
            except ValidationError:
                raise ServiceFault(ServiceErrorCode.VALIDATION) from None
            changes.append((reference, None, None))
            current_run = current_run.model_copy(update={"result_version": result.result_version})
        elif result is not None:
            raise ValueError("Only publication commits a result reference")
        if transition.status == JobStatus.FAILED and problem is None:
            raise ValueError("Terminal failure requires a sanitized problem")
        if transition.status == JobStatus.CANCELLED:
            problem = ServiceProblem(code=ServiceErrorCode.CONFLICT)
        elif transition.status != JobStatus.FAILED:
            problem = None
        current_job = job.model_copy(
            update={
                "status": transition.status,
                "attempt_count": job.attempt_count + transition.attempt_delta,
                "problem": problem,
                "updated_at": now,
                "open_task_ids": ()
                if transition.status == JobStatus.CANCELLED
                else tuple(dict.fromkeys(open_task_ids)),
            }
        )
        if transition.release_lease:
            current_run = _release(current_run)
        changes.extend(
            [
                (current_job, job, None),
                (current_run, run, self._lease_condition(attempt, now)),
                await self._close_attempt(run, event, now),
            ]
        )
        await self._write(changes)
        return current_job.record(current_run)

    async def expire_lease(self, lease: ExpiredLease, *, now: int) -> JobRecord:
        job, run = await self._required(lease.job_id)
        if (
            run.run_id != lease.run_id
            or run.fencing_token != lease.fencing_token
            or run.lease_expires_at != lease.lease_expires_at
            or run.lease_expires_at > now
            or job.status != JobStatus.RUNNING
        ):
            raise ConditionFailed("Lease candidate is stale or still valid")
        transition = next_state(_facts(job), JobEvent.LEASE_EXPIRED, policy=self.policy)
        current_job = job.model_copy(
            update={
                "status": transition.status,
                "updated_at": now,
                "lease_takeover_count": job.lease_takeover_count + transition.takeover_delta,
            }
        )
        if transition.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
            current_job = current_job.model_copy(
                update={
                    "problem": ServiceProblem(
                        code=ServiceErrorCode.EXECUTION
                        if transition.status == JobStatus.FAILED
                        else ServiceErrorCode.CONFLICT
                    ),
                    "open_task_ids": (),
                }
            )
        changes: list[Change] = [await self._close_attempt(run, JobEvent.LEASE_EXPIRED, now)]
        if transition.enqueue_outbox:
            current_job, outbox = self._enqueue(current_job, run.run_id, now)
            changes.append((outbox, None, None))
        current_run = _release(run.model_copy(update={"status": transition.status}))
        changes.extend(
            [
                (current_job, job, None),
                (
                    current_run,
                    run,
                    Attr("lease_expires_at").eq(lease.lease_expires_at)
                    & Attr("lease_expires_at").lte(now),
                ),
            ]
        )
        await self._write(changes)
        return current_job.record(current_run)

    async def cancel(self, *, job_id: UUID, now: int) -> JobRecord:
        job, run = await self._required(job_id)
        transition = next_state(_facts(job), JobEvent.CANCEL, policy=self.policy)
        current_job = job.model_copy(update={"status": transition.status, "updated_at": now})
        current_run = run.model_copy(update={"status": transition.status})
        if transition.request_cancel:
            current_job = current_job.model_copy(update={"cancel_requested": True})
        else:
            current_job = current_job.model_copy(
                update={
                    "problem": ServiceProblem(code=ServiceErrorCode.CONFLICT),
                    "open_task_ids": (),
                }
            )
        if transition.release_lease:
            current_run = _release(current_run)
        await self._write([(current_job, job, None), (current_run, run, None)])
        return current_job.record(current_run)

    async def schedule_retry(self, *, job_id: UUID, available_at: int, now: int) -> JobRecord:
        job, run = await self._required(job_id)
        try:
            transition = next_state(_facts(job), JobEvent.SCHEDULE_RETRY, policy=self.policy)
        except ServiceFault:
            raise ConditionFailed("Job no longer awaits retry") from None
        current_job = job.model_copy(
            update={"status": transition.status, "problem": None, "updated_at": now}
        )
        current_job, outbox = self._enqueue(current_job, run.run_id, available_at)
        current_run = run.model_copy(update={"status": transition.status})
        await self._write(
            [(current_job, job, None), (current_run, run, None), (outbox, None, None)]
        )
        return current_job.record(current_run)

    async def reject_human_task(
        self, *, job_id: UUID, run_id: UUID, task_id: UUID, now: int
    ) -> JobRecord:
        job, run = await self._required(job_id)
        if run.run_id != run_id or task_id not in job.open_task_ids:
            raise ConditionFailed("The task no longer belongs to the current open run")
        remaining = tuple(item for item in job.open_task_ids if item != task_id)
        transition = next_state(
            replace(_facts(job), has_open_tasks=bool(remaining)),
            JobEvent.HUMAN_REJECTED,
            policy=self.policy,
        )
        current_job = job.model_copy(
            update={
                "open_task_ids": remaining,
                "status": transition.status,
                "problem": ServiceProblem(code=ServiceErrorCode.CONFLICT)
                if transition.status == JobStatus.FAILED
                else None,
                "updated_at": now,
            }
        )
        current_run = run.model_copy(update={"status": transition.status})
        await self._write([(current_job, job, None), (current_run, run, None)])
        return current_job.record(current_run)

    async def resume_after_human(self, *, job_id: UUID, run: RunReference, now: int) -> JobRecord:
        job, previous = await self._required(job_id)
        try:
            run = RunReference.model_validate_json(run.model_dump_json())
        except ValidationError:
            raise ServiceFault(ServiceErrorCode.VALIDATION) from None
        if (
            run.run_id == previous.run_id
            or run.revision == previous.revision
            or run.revision.case_id != job.case_id
            or run.attempt_id is not None
        ):
            raise ConditionFailed("Resume requires a new run and revision")
        transition = next_state(_facts(job), JobEvent.HUMAN_RESPONSE_COMMITTED, policy=self.policy)
        current_run = RunRow(
            job_id=job_id,
            run_id=run.run_id,
            revision=run.revision,
            documents=previous.documents,
            payload_digest=submission_digest(
                ReviewSubmission(
                    revision=run.revision,
                    documents=previous.documents,
                    idempotency_key=f"run-{run.run_id}",
                )
            ),
            status=transition.status,
        )
        current_job = job.model_copy(
            update={
                "status": transition.status,
                "current_run_id": run.run_id,
                "open_task_ids": (),
                "updated_at": now,
            }
        )
        current_job, outbox = self._enqueue(current_job, run.run_id, now)
        await self._write(
            [(current_job, job, None), (current_run, None, None), (outbox, None, None)]
        )
        return current_job.record(current_run)

    async def _outbox(self, record: DispatchRecord) -> OutboxRow:
        row = await self._get(
            {
                "pk": f"JOB#{record.job_id}",
                "sk": f"OUTBOX#{record.outbox_seq:020d}",
            },
            OutboxRow,
        )
        if row is None or (
            row.run_id != record.run_id
            or row.dispatch_token != record.dispatch_token
            or row.dispatch_state != "pending"
            or row.available_at != record.available_at
            or row.dispatch_attempts != record.dispatch_attempts
        ):
            raise ConditionFailed("Dispatch candidate is stale")
        return row

    async def mark_dispatched(self, record: DispatchRecord, *, now: int) -> JobRecord:
        job, run = await self._required(record.job_id)
        outbox = await self._outbox(record)
        current_job = job.model_copy(update={"updated_at": now})
        current_run = run
        # Late confirmation for an old run must not move the new run to dispatched.
        if job.status == JobStatus.QUEUED and outbox.run_id == run.run_id:
            transition = next_state(_facts(job), JobEvent.DISPATCH_SUCCEEDED, policy=self.policy)
            current_job = current_job.model_copy(update={"status": transition.status})
            current_run = run.model_copy(update={"status": transition.status})
        await self._write(
            [
                (current_job, job, None),
                (current_run, run, None),
                (outbox.model_copy(update={"dispatch_state": "sent"}), outbox, None),
            ]
        )
        return current_job.record(current_run)

    async def reschedule_dispatch(self, record: DispatchRecord, *, available_at: int) -> bool:
        job, run = await self._required(record.job_id)
        outbox = await self._outbox(record)
        active = job.status not in TERMINAL_STATUSES
        if active:
            next_state(_facts(job), JobEvent.DISPATCH_FAILED, policy=self.policy)
            updated = outbox.model_copy(
                update={
                    "available_at": available_at,
                    "dispatch_attempts": outbox.dispatch_attempts + 1,
                }
            )
        else:
            updated = outbox.model_copy(update={"dispatch_state": "abandoned"})
        # Including unchanged job/run rows still applies CAS against concurrent completion.
        await self._write([(job, job, None), (run, run, None), (updated, outbox, None)])
        return active

    async def _candidates(self, partition: str, cutoff: int, limit: int) -> list[dict[str, str]]:
        if not 1 <= limit <= 1000:
            raise ValueError("Recovery limit must be between 1 and 1000")

        def query() -> list[dict[str, str]]:
            pages = self.client.get_paginator("query").paginate(
                TableName=self.table_name,
                IndexName=self.index_name,
                **_expression(
                    Key("recovery_pk").eq(partition) & Key("recovery_at").lte(cutoff), query=True
                ),
                ProjectionExpression="pk, sk",
                ScanIndexForward=True,
                PaginationConfig={"MaxItems": limit, "PageSize": min(limit, 100)},
            )
            return [
                {"pk": item["pk"]["S"], "sk": item["sk"]["S"]}
                for page in pages
                for item in page.get("Items", [])
            ]

        try:
            return await asyncio.to_thread(query)
        except (ClientError, BotoCoreError) as error:
            raise _storage_error(error) from None
        except (KeyError, TypeError, ValueError):
            raise DynamoDBJobStoreError("corrupt") from None

    async def pending_dispatches(self, *, now: int, limit: int) -> tuple[DispatchRecord, ...]:
        records = []
        for key in await self._candidates("PENDING", now, limit):
            row = await self._get(key, OutboxRow)
            if row is not None and row.dispatch_state == "pending" and row.available_at <= now:
                records.append(row.record())
        records.sort(key=lambda item: (item.available_at, item.outbox_seq))
        return tuple(records)

    async def expired_leases(self, *, now: int, limit: int) -> tuple[ExpiredLease, ...]:
        records = []
        for key in await self._candidates("LEASE", now, limit):
            row = await self._get(key, RunRow)
            if row is not None and row.lease_expires_at is not None and row.lease_expires_at <= now:
                records.append(
                    ExpiredLease(
                        job_id=row.job_id,
                        run_id=row.run_id,
                        fencing_token=row.fencing_token,
                        lease_expires_at=row.lease_expires_at,
                    )
                )
        records.sort(key=lambda item: item.lease_expires_at)
        return tuple(records)

    async def stranded_retryables(
        self,
        *,
        stranded_before: int,
        limit: int,
    ) -> tuple[JobRecord, ...]:
        records = []
        for key in await self._candidates("RETRY", stranded_before, limit):
            row = await self._get(key, JobRow)
            if row is None:
                continue
            snapshot = await self._snapshot(row.job_id)
            if snapshot is not None:
                job, run = snapshot
                if job.status == JobStatus.RETRYABLE_FAILED and job.updated_at <= stranded_before:
                    records.append((job.updated_at, job.record(run)))
        records.sort(key=lambda item: item[0])
        return tuple(record for _, record in records)
