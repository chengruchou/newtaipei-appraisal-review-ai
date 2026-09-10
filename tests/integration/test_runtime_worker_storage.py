"""Offline Runtime worker with real adapter code and emulated DynamoDB/S3 services.

Executors below are isolated test inputs. They do not stand in for a configured
production executor, reviewed document authority or human acceptance evidence.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import threading
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import boto3
import pytest
from botocore.client import BaseClient
from botocore.config import Config
from botocore.response import StreamingBody
from botocore.stub import Stubber
from moto import mock_aws

from appraisal_review.adapters.aws.job_store import DynamoDBJobStore
from appraisal_review.adapters.aws.result_store import S3ResultStore
from appraisal_review.application.job_state import JobPolicy
from appraisal_review.application.outbox import DispatchMessage
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.runtime_worker import ExecutedReview, RuntimeWorker
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.factor_models import EvaluationStatus, WorkflowStatus
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    RunReference,
    ServiceErrorCode,
    ServiceResult,
    ServiceVerification,
    VerificationDiagnostic,
)
from appraisal_review.ports.jobs import ClaimedAttempt, ConditionFailed, JobRecord
from appraisal_review.testing.job_store_contract import NOW, principal, submission

TABLE = "synthetic-worker-jobs"
BUCKET = "synthetic-worker-results"
ACCOUNT = "123456789012"
CANARY = "synthetic-private-diagnostic"


def sdk_client(service: str) -> Any:
    return boto3.session.Session().client(
        service,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        config=Config(retries={"total_max_attempts": 1}, connect_timeout=1, read_timeout=1),
    )


@dataclass
class World:
    db: Any
    s3: Any
    jobs: DynamoDBJobStore
    results: S3ResultStore
    service: ReviewJobService
    clock: list[int]

    def reconstructed(self) -> World:
        return make_world(sdk_client("dynamodb"), sdk_client("s3"), self.clock, self.service.policy)


def make_world(db: Any, s3: Any, clock: list[int], policy: JobPolicy | None = None) -> World:
    jobs = DynamoDBJobStore(db, table_name=TABLE, policy=policy)
    results = S3ResultStore(s3, bucket=BUCKET, account_id=ACCOUNT, jobs=jobs)
    service = ReviewJobService(
        jobs, results, clock=lambda: clock[0], jitter=lambda _: 0, policy=policy
    )
    return World(db, s3, jobs, results, service, clock)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    # Moto has rollback but no thread isolation. Emulate atomic individual service
    # operations, leaving adapter read/decide/write sequences free to interleave.
    lock = threading.RLock()
    original = BaseClient._make_api_call

    def atomic(self: Any, operation_name: str, api_params: Any) -> Any:
        with lock:
            return original(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", atomic)
    with mock_aws():
        db, s3 = sdk_client("dynamodb"), sdk_client("s3")
        db.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": key, "AttributeType": kind}
                for key, kind in (
                    ("pk", "S"),
                    ("sk", "S"),
                    ("recovery_pk", "S"),
                    ("recovery_at", "N"),
                )
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "job-recovery",
                    "KeySchema": [
                        {"AttributeName": "recovery_pk", "KeyType": "HASH"},
                        {"AttributeName": "recovery_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        s3.create_bucket(Bucket=BUCKET)
        s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        policy = JobPolicy(
            lease_seconds=3,
            heartbeat_seconds=1,
            visibility_timeout_seconds=4,
            backoff_base_seconds=1,
        )
        yield make_world(db, s3, [NOW], policy)


async def admit(world: World) -> tuple[JobRecord, DispatchMessage]:
    accepted = await world.service.submit(principal(), submission())
    record = await world.jobs.read_job(job_id=accepted.status.job.job_id)
    assert record is not None
    outbox = (await world.service.due_dispatches())[0]
    await world.service.confirm_dispatch(outbox)
    return record, DispatchMessage(
        job_id=record.job_id,
        run_id=record.current_run.run_id,
        outbox_seq=outbox.outbox_seq,
        dispatch_token=outbox.dispatch_token,
        enqueued_at=world.clock[0],
    )


async def acquire(world: World, record: JobRecord) -> ClaimedAttempt:
    return await world.service.claim(
        job_id=record.job_id, run_id=record.current_run.run_id, owner=uuid4()
    )


def result_for(attempt: ClaimedAttempt) -> ServiceResult:
    return ServiceResult(
        run=RunReference(
            run_id=attempt.run_id, attempt_id=attempt.attempt_id, revision=submission().revision
        ),
        result_version=attempt.expected_result_version + 1,
        execution_status=ExecutionStatus.SUCCEEDED,
        business_status=WorkflowStatus.NEEDS_REVIEW,
        verification=ServiceVerification(
            status=EvaluationStatus.NEEDS_REVIEW,
            warnings=(
                VerificationDiagnostic(
                    code="verification_warning",
                    message=(
                        "Verification reported a warning; request human review before proceeding."
                    ),
                ),
            ),
        ),
        durable=True,
    )


def serialized(result: ServiceResult) -> bytes:
    return json.dumps(
        result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()


def object_key(result: ServiceResult) -> str:
    return f"results/{result.run.run_id}/{result.result_version}/{content_digest(result)}.json"


def objects(world: World) -> list[dict[str, Any]]:
    return world.s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])


def test_orphan_written_before_winner_cannot_block_or_replace_fenced_publication(
    world: World,
) -> None:
    async def scenario() -> None:
        record, _ = await admit(world)
        stale = await acquire(world, record)
        orphan = result_for(stale)
        await world.results.put(run_id=stale.run_id, result_version=1, result=orphan)
        assert len(objects(world)) == 1
        assert (
            await world.reconstructed().results.get(run_id=stale.run_id, result_version=1) is None
        )

        world.clock[0] += 3
        await world.service.reclaim_expired_leases()
        current_world = world.reconstructed()
        current = await acquire(current_world, record)
        winner = result_for(current)
        assert content_digest(winner) != content_digest(orphan)
        assert current.fencing_token == 2
        published = await current_world.service.publish(current, winner)
        assert published.status == JobStatus.SUCCEEDED
        assert {item["Key"] for item in objects(world)} == {object_key(orphan), object_key(winner)}

        final_world = world.reconstructed()
        fetched = await final_world.service.result(principal(), record.job_id)
        assert fetched == winner and fetched.verification is not None
        assert len(fetched.verification.warnings) == 1
        assert fetched.business_status == WorkflowStatus.NEEDS_REVIEW
        assert fetched.artifact_status == "not_requested"
        with pytest.raises(ConditionFailed):
            await final_world.service.publish(stale, orphan)
        reference = await final_world.jobs.read_result_reference(
            run_id=stale.run_id, result_version=1
        )
        assert reference is not None and reference.result_digest == content_digest(winner)
        assert reference.fencing_token == 2
        assert (
            await world.reconstructed().results.get(run_id=stale.run_id, result_version=1) == winner
        )

    asyncio.run(scenario())


def test_identical_s3_replay_preserves_one_immutable_version_and_stays_unpublished(
    world: World,
) -> None:
    async def scenario() -> None:
        record, _ = await admit(world)
        attempt = await acquire(world, record)
        body = result_for(attempt)
        first = await world.results.put(run_id=attempt.run_id, result_version=1, result=body)
        again = await world.reconstructed().results.put(
            run_id=attempt.run_id, result_version=1, result=body
        )
        assert first == again == content_digest(body)
        versions = world.s3.list_object_versions(Bucket=BUCKET).get("Versions", [])
        assert len(versions) == 1
        assert await world.results.get(run_id=attempt.run_id, result_version=1) is None
        with pytest.raises(ServiceFault) as caught:
            await world.service.result(principal(), record.job_id)
        assert caught.value.problem.code == ServiceErrorCode.CONFLICT

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["AccessDenied", "SlowDown", "RequestTimeout", "InternalError"])
def test_s3_sdk_write_shape_and_safe_error_preserve_uncommitted_job(
    world: World, code: str
) -> None:
    async def scenario() -> None:
        record, _ = await admit(world)
        attempt = await acquire(world, record)
        result = result_for(attempt)
        sdk = sdk_client("s3")
        results = S3ResultStore(sdk, bucket=BUCKET, account_id=ACCOUNT, jobs=world.jobs)
        data = serialized(result)
        with Stubber(sdk) as stub:
            stub.add_client_error(
                "put_object",
                service_error_code=code,
                service_message=CANARY,
                expected_params={
                    "Bucket": BUCKET,
                    "ExpectedBucketOwner": ACCOUNT,
                    "Key": object_key(result),
                    "Body": data,
                    "IfNoneMatch": "*",
                    "ContentType": "application/json",
                    "ServerSideEncryption": "AES256",
                    "ChecksumAlgorithm": "SHA256",
                    "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
                },
            )
            with pytest.raises(ServiceFault) as caught:
                await results.put(run_id=attempt.run_id, result_version=1, result=result)
            assert caught.value.problem.code == ServiceErrorCode.EXECUTION
            assert CANARY not in str(caught.value)
            assert caught.value.__suppress_context__
            stub.assert_no_pending_responses()
        assert (await world.jobs.read_job(job_id=record.job_id)).status == JobStatus.RUNNING
        assert (
            await world.jobs.read_result_reference(run_id=attempt.run_id, result_version=1) is None
        )
        assert objects(world) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "damage", ["checksum", "length", "version", "encryption", "digest", "oversize", "malformed"]
)
def test_committed_s3_body_integrity_failures_close_stream_and_fail_safely(
    world: World, damage: str
) -> None:
    async def scenario() -> None:
        record, _ = await admit(world)
        attempt = await acquire(world, record)
        result = result_for(attempt)
        await world.service.publish(attempt, result)
        data = serialized(result)
        if damage == "digest":
            data = serialized(result.model_copy(update={"business_status": WorkflowStatus.FAILED}))
        if damage == "malformed":
            data = CANARY.encode()
        stream = io.BytesIO(data)
        response = {
            "Body": StreamingBody(stream, len(data)),
            "ContentLength": len(data),
            "VersionId": "synthetic-version",
            "ServerSideEncryption": "AES256",
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
        }
        if damage == "checksum":
            response["ChecksumSHA256"] = base64.b64encode(b"wrong").decode()
        if damage == "length":
            response["ContentLength"] = len(data) + 1
        if damage == "version":
            response["VersionId"] = "null"
        if damage == "encryption":
            del response["ServerSideEncryption"]
        sdk = sdk_client("s3")
        results = S3ResultStore(
            sdk,
            bucket=BUCKET,
            account_id=ACCOUNT,
            jobs=world.jobs,
            max_bytes=16 if damage == "oversize" else 1024 * 1024,
        )
        with Stubber(sdk) as stub:
            stub.add_response(
                "get_object",
                response,
                {
                    "Bucket": BUCKET,
                    "ExpectedBucketOwner": ACCOUNT,
                    "Key": object_key(result),
                    "ChecksumMode": "ENABLED",
                },
            )
            with pytest.raises(ServiceFault) as caught:
                await results.get(run_id=attempt.run_id, result_version=1)
            assert caught.value.problem.code == ServiceErrorCode.EXECUTION
            assert CANARY not in str(caught.value)
            assert stream.closed
            stub.assert_no_pending_responses()
        # Bad object responses cannot modify the authoritative pointer or create success.
        reference = await world.jobs.read_result_reference(run_id=attempt.run_id, result_version=1)
        assert reference is not None and reference.result_digest == content_digest(result)

    asyncio.run(scenario())


def test_invalid_result_model_is_sanitized_before_s3_write(world: World) -> None:
    async def scenario() -> None:
        record, _ = await admit(world)
        attempt = await acquire(world, record)
        invalid = result_for(attempt).model_copy(update={"result_version": CANARY})
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            with pytest.raises(ServiceFault) as caught:
                await world.results.put(run_id=attempt.run_id, result_version=1, result=invalid)
        assert caught.value.problem.code == ServiceErrorCode.VALIDATION
        assert CANARY not in str(caught.value)
        assert all(CANARY not in str(warning.message) for warning in emitted)
        assert objects(world) == []

    asyncio.run(scenario())


class SuccessfulExecution:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
        self.calls += 1
        assert record.current_run.attempt_id == attempt.attempt_id
        # The application, not an executor assertion, marks the persisted body durable.
        return ExecutedReview(result=result_for(attempt).model_copy(update={"durable": False}))


class BlockingExecution(SuccessfulExecution):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = asyncio.Event()

    async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
        self.started.set()
        try:
            await self.release.wait()
            return await super().execute(record, attempt)
        finally:
            self.stopped.set()


def test_duplicate_worker_delivery_executes_once_and_reconstructed_worker_stays_idle(
    world: World,
) -> None:
    async def scenario() -> None:
        record, message = await admit(world)
        execution = BlockingExecution()
        running = asyncio.create_task(RuntimeWorker(world.service, execution).process(message))
        await asyncio.wait_for(execution.started.wait(), 2)
        duplicate = SuccessfulExecution()
        assert (
            await RuntimeWorker(world.reconstructed().service, duplicate).process(message)
            == "superseded"
        )
        execution.release.set()
        assert await asyncio.wait_for(running, 2) == "published"
        assert execution.calls == 1 and duplicate.calls == 0
        assert (
            await RuntimeWorker(world.reconstructed().service, duplicate).process(message)
            == "superseded"
        )
        assert duplicate.calls == 0
        state = await world.jobs.read_job(job_id=record.job_id)
        assert (
            state is not None and state.status == JobStatus.SUCCEEDED and state.result_version == 1
        )
        assert len(objects(world)) == 1
        result = await world.reconstructed().service.result(principal(), record.job_id)
        assert result.durable is True
        assert result.verification is not None and len(result.verification.warnings) == 1

    asyncio.run(scenario())


def test_worker_cooperatively_cancels_and_never_publishes(world: World) -> None:
    async def scenario() -> None:
        record, message = await admit(world)
        execution = BlockingExecution()
        running = asyncio.create_task(RuntimeWorker(world.service, execution).process(message))
        await asyncio.wait_for(execution.started.wait(), 2)
        flagged = await world.reconstructed().service.cancel(principal(), record.job_id)
        assert flagged.cancel_requested
        assert await asyncio.wait_for(running, 3) == "cancelled"
        assert execution.stopped.is_set()
        state = await world.reconstructed().jobs.read_job(job_id=record.job_id)
        assert state is not None and state.status == JobStatus.CANCELLED
        assert state.attempt_count == 0 and state.result_version == 0
        assert objects(world) == []
        assert await world.jobs.expired_leases(now=NOW + 100, limit=5) == ()
        assert await world.jobs.pending_dispatches(now=NOW + 100, limit=5) == ()

    asyncio.run(scenario())


def test_worker_loses_expired_lease_and_cancels_execution_without_overwriting_winner(
    world: World,
) -> None:
    async def scenario() -> None:
        record, message = await admit(world)
        execution = BlockingExecution()
        running = asyncio.create_task(RuntimeWorker(world.service, execution).process(message))
        await asyncio.wait_for(execution.started.wait(), 2)
        world.clock[0] += 3
        replacement = world.reconstructed()
        assert len(await replacement.service.reclaim_expired_leases()) == 1
        winner = await acquire(replacement, record)
        result = result_for(winner)
        await replacement.service.publish(winner, result)
        assert await asyncio.wait_for(running, 3) == "superseded"
        assert execution.stopped.is_set() and execution.calls == 0
        assert await world.reconstructed().service.result(principal(), record.job_id) == result
        assert len(objects(world)) == 1

    asyncio.run(scenario())


def test_worker_timeout_closes_execution_and_schedules_durable_retry(world: World) -> None:
    async def scenario() -> None:
        record, message = await admit(world)
        execution = BlockingExecution()
        outcome = await asyncio.wait_for(
            RuntimeWorker(
                world.service,
                execution,
                timeout_seconds=0.01,
            ).process(message),
            2,
        )
        assert outcome == "retry_scheduled" and execution.stopped.is_set()
        state = await world.reconstructed().jobs.read_job(job_id=record.job_id)
        assert state is not None and state.status == JobStatus.QUEUED and state.attempt_count == 1
        assert await world.jobs.pending_dispatches(now=NOW, limit=5) == ()
        due = await world.jobs.pending_dispatches(now=NOW + 1, limit=5)
        assert len(due) == 1 and due[0].outbox_seq == 2
        assert objects(world) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error,expected,attempts",
    [
        (ServiceErrorCode.UNAUTHORIZED, "failed", 0),
        (ServiceErrorCode.NOT_FOUND, "failed", 0),
        (ServiceErrorCode.VALIDATION, "failed", 0),
        (ServiceErrorCode.CAPABILITY, "retry_scheduled", 1),
        (ServiceErrorCode.EXECUTION, "retry_scheduled", 1),
        (None, "retry_scheduled", 1),
    ],
)
def test_worker_failure_classification_is_sanitized_and_durable(
    world: World, error: Any, expected: str, attempts: int
) -> None:
    class FailingExecution:
        async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
            if error is None:
                raise RuntimeError(CANARY)
            raise ServiceFault(error)

    async def scenario() -> None:
        record, message = await admit(world)
        assert await RuntimeWorker(world.service, FailingExecution()).process(message) == expected
        state = await world.reconstructed().jobs.read_job(job_id=record.job_id)
        assert state is not None and state.attempt_count == attempts
        assert state.status == (JobStatus.FAILED if expected == "failed" else JobStatus.QUEUED)
        snapshot = world.db.scan(TableName=TABLE)["Items"]
        assert CANARY not in json.dumps(snapshot)
        assert state.result_version == 0 and objects(world) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [None, ServiceErrorCode.EXECUTION])
def test_worker_reports_terminal_exhaustion_as_failed_instead_of_scheduled(
    world: World, error: Any
) -> None:
    class FailingExecution:
        async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
            if error is None:
                raise RuntimeError(CANARY)
            raise ServiceFault(error)

    async def scenario() -> None:
        bounded = make_world(world.db, world.s3, world.clock, JobPolicy(max_attempts=1))
        record, message = await admit(bounded)
        assert await RuntimeWorker(bounded.service, FailingExecution()).process(message) == "failed"
        state = await bounded.jobs.read_job(job_id=record.job_id)
        assert state is not None and state.status == JobStatus.FAILED and state.attempt_count == 1
        assert await bounded.jobs.pending_dispatches(now=NOW + 1000, limit=5) == ()
        assert objects(world) == []

    asyncio.run(scenario())


def test_worker_refuses_a_result_bound_to_another_attempt(world: World) -> None:
    class WrongAttemptExecution:
        async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
            result = result_for(attempt)
            return ExecutedReview(
                result=result.model_copy(
                    update={
                        "run": result.run.model_copy(update={"attempt_id": uuid4()}),
                    }
                )
            )

    async def scenario() -> None:
        record, message = await admit(world)
        assert (
            await RuntimeWorker(world.service, WrongAttemptExecution()).process(message) == "failed"
        )
        state = await world.jobs.read_job(job_id=record.job_id)
        assert state is not None and state.problem is not None
        assert state.problem.code == ServiceErrorCode.VALIDATION
        assert objects(world) == []

    asyncio.run(scenario())


def test_worker_waits_for_persisted_human_tasks_and_releases_lease(world: World) -> None:
    task_ids = (uuid4(), uuid4())

    class HumanExecution:
        async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
            return ExecutedReview(persisted_task_ids=task_ids)

    async def scenario() -> None:
        record, message = await admit(world)
        assert (
            await RuntimeWorker(world.service, HumanExecution()).process(message)
            == "waiting_for_human"
        )
        state = await world.reconstructed().jobs.read_job(job_id=record.job_id)
        assert state is not None and state.status == JobStatus.WAITING_FOR_HUMAN
        assert state.open_task_ids == task_ids and state.attempt_count == 0
        assert await world.jobs.expired_leases(now=NOW + 1000, limit=5) == ()
        assert objects(world) == []

    asyncio.run(scenario())


def test_cancel_after_claim_before_executor_launch_starts_no_execution(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        record, message = await admit(world)
        original = world.service.claim

        async def cancelled_claim(**kwargs: Any) -> ClaimedAttempt:
            attempt = await original(**kwargs)
            await world.reconstructed().service.cancel(principal(), record.job_id)
            return attempt

        monkeypatch.setattr(world.service, "claim", cancelled_claim)
        execution = SuccessfulExecution()
        assert await RuntimeWorker(world.service, execution).process(message) == "cancelled"
        assert execution.calls == 0
        state = await world.jobs.read_job(job_id=record.job_id)
        assert state is not None and state.status == JobStatus.CANCELLED
        assert objects(world) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [None, ServiceErrorCode.EXECUTION])
def test_cancel_arriving_during_failure_reports_cancelled_without_retry(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    error: Any,
) -> None:
    class FailingExecution:
        async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
            if error is None:
                raise RuntimeError(CANARY)
            raise ServiceFault(error)

    async def scenario() -> None:
        record, message = await admit(world)
        original = world.service.fail

        async def cancelled_failure(attempt: ClaimedAttempt, **kwargs: Any) -> JobRecord:
            await world.reconstructed().service.cancel(principal(), record.job_id)
            return await original(attempt, **kwargs)

        monkeypatch.setattr(world.service, "fail", cancelled_failure)
        assert (
            await RuntimeWorker(world.service, FailingExecution()).process(message) == "cancelled"
        )
        state = await world.jobs.read_job(job_id=record.job_id)
        assert (
            state is not None and state.status == JobStatus.CANCELLED and state.attempt_count == 0
        )
        assert await world.jobs.pending_dispatches(now=NOW + 1000, limit=5) == ()
        assert objects(world) == []

    asyncio.run(scenario())


def test_worker_invalid_result_serialization_emits_no_private_warning(
    world: World,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class MalformedExecution:
        async def execute(self, record: JobRecord, attempt: ClaimedAttempt) -> ExecutedReview:
            result = result_for(attempt).model_copy(update={"result_version": CANARY})
            return ExecutedReview(result=result)

    async def scenario() -> None:
        record, message = await admit(world)
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            outcome = await RuntimeWorker(world.service, MalformedExecution()).process(message)
        assert outcome == "retry_scheduled"
        assert emitted == []
        assert CANARY not in caplog.text
        state = await world.reconstructed().jobs.read_job(job_id=record.job_id)
        assert state is not None and state.status == JobStatus.QUEUED
        assert state.attempt_count == 1 and state.result_version == 0
        assert CANARY not in json.dumps(world.db.scan(TableName=TABLE)["Items"])
        assert objects(world) == []

    asyncio.run(scenario())
