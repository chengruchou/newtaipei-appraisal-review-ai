"""Offline SDK/emulated persistence evidence; this is not live DynamoDB acceptance.

Moto implements rollback but not concurrent transaction isolation. Serialize individual
SDK calls in the emulator only; store reads and writes remain independently interleaved,
and the forced races below make every contender read the same pre-transaction versions.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from uuid import uuid4

import boto3
import pytest
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import ANY, Stubber
from moto import mock_aws

from appraisal_review.adapters.aws.job_serialization import (
    AttemptRow,
    JobRow,
    RunRow,
    decode,
    encode,
    marshal,
)
from appraisal_review.adapters.aws.job_store import DynamoDBJobStore, DynamoDBJobStoreError
from appraisal_review.application.job_state import JobEvent, JobPolicy
from appraisal_review.application.service_guards import ServiceFault, submission_digest
from appraisal_review.domain.job_contracts import JobStatus
from appraisal_review.domain.service_contracts import (
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
)
from appraisal_review.ports.jobs import ClaimedAttempt, ConditionFailed, JobStore
from appraisal_review.testing.job_store_contract import (
    CONTRACT_CHECKS,
    NOW,
    JobStoreContract,
    principal,
    result_reference,
    submission,
)

TABLE = "synthetic-job-contract"


def client() -> Any:
    # Fixed synthetic credentials prevent all credential-provider/network lookups.
    return boto3.session.Session().client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        config=Config(retries={"total_max_attempts": 1}, connect_timeout=1, read_timeout=1),
    )


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    lock = threading.RLock()
    original = BaseClient._make_api_call

    def isolated(self: Any, operation_name: str, api_params: Any) -> Any:
        with lock:
            return original(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", isolated)
    with mock_aws():
        sdk = client()
        sdk.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": kind}
                for name, kind in (
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
        yield sdk


def store(db: Any, **kwargs: Any) -> DynamoDBJobStore:
    return DynamoDBJobStore(db, table_name=TABLE, **kwargs)


async def create(adapter: JobStore, *, key: str = "synthetic-request") -> Any:
    record, _ = await adapter.create_job(
        principal(),
        submission(key=key),
        job_id=uuid4(),
        run_id=uuid4(),
        now=NOW,
    )
    return record


async def claim(adapter: JobStore, record: Any, *, now: int = NOW) -> ClaimedAttempt:
    return await adapter.claim(
        job_id=record.job_id,
        run_id=record.current_run.run_id,
        owner=uuid4(),
        lease_seconds=120,
        now=now,
    )


def rows(db: Any) -> list[dict[str, Any]]:
    # Full scans are test-only, to inspect the entire stored plane for atomicity/privacy.
    return [
        item
        for page in db.get_paginator("scan").paginate(TableName=TABLE)
        for item in page["Items"]
    ]


@pytest.mark.parametrize("check", CONTRACT_CHECKS)
def test_shared_job_store_contract(db: Any, check: str) -> None:
    adapter: JobStore = store(db)
    asyncio.run(getattr(JobStoreContract(), check)(adapter))


def test_fresh_clients_and_adapters_recover_independent_rows_and_publication(db: Any) -> None:
    async def scenario() -> None:
        record = await create(store(db))
        restored = store(client())
        assert await restored.read_job(job_id=record.job_id) == record
        pending = await restored.pending_dispatches(now=NOW, limit=10)
        await restored.mark_dispatched(pending[0], now=NOW)
        attempt = await claim(store(client()), record)
        reference = result_reference(attempt.run_id, version=1, token=attempt.fencing_token)
        published = await store(client()).finish(
            attempt,
            event=JobEvent.PUBLISH_RESULT,
            result=reference,
            now=NOW + 1,
        )
        assert await store(client()).read_job(job_id=record.job_id) == published
        assert (
            await store(client()).read_result_reference(run_id=attempt.run_id, result_version=1)
            == reference
        )
        assert await store(client()).pending_dispatches(now=NOW + 1000, limit=10) == ()
        assert await store(client()).expired_leases(now=NOW + 1000, limit=10) == ()
        snapshot = rows(db)
        assert sorted(item["kind"]["S"] for item in snapshot) == [
            "attempt",
            "idempotency",
            "job",
            "outbox",
            "result",
            "run",
        ]
        assert all("recovery_pk" not in item and "recovery_at" not in item for item in snapshot)
        history = decode(next(row for row in snapshot if row["kind"]["S"] == "attempt"), AttemptRow)
        assert history.outcome == "publish_result" and history.closed_at == NOW + 1
        assert "synthetic-request" not in json.dumps(snapshot)
        assert not {"submission", "result", "body", "raw_text", "idempotency_key"} & {
            key for row in snapshot for key in row
        }

    asyncio.run(scenario())


def test_forced_claim_race_across_independent_adapters_has_one_durable_winner(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        record = await create(store(db))
        barrier = asyncio.Barrier(8)

        async def contender() -> Any:
            adapter = store(client())
            required = adapter._required

            async def same_snapshot(job_id: Any) -> Any:
                snapshot = await required(job_id)
                await barrier.wait()
                return snapshot

            monkeypatch.setattr(adapter, "_required", same_snapshot)
            try:
                return await claim(adapter, record)
            except ConditionFailed as error:
                return error

        outcomes = await asyncio.gather(*(contender() for _ in range(8)))
        winners = [outcome for outcome in outcomes if isinstance(outcome, ClaimedAttempt)]
        assert len(winners) == 1
        assert sum(isinstance(item, ConditionFailed) for item in outcomes) == 7
        attempts = [decode(row, AttemptRow) for row in rows(db) if row["kind"]["S"] == "attempt"]
        assert len(attempts) == 1 and attempts[0].attempt_id == winners[0].attempt_id
        assert attempts[0].fencing_token == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("offset", [120, 121, 10000])
@pytest.mark.parametrize("operation", ["heartbeat", "finish"])
def test_expired_authority_is_rejected_even_before_takeover(
    db: Any, offset: int, operation: str
) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        attempt = await claim(adapter, record)
        before = rows(db)
        with pytest.raises(ConditionFailed):
            if operation == "heartbeat":
                await store(client()).heartbeat(attempt, now=NOW + offset, lease_seconds=120)
            else:
                await store(client()).finish(
                    attempt,
                    event=JobEvent.PUBLISH_RESULT,
                    now=NOW + offset,
                    result=result_reference(attempt.run_id, version=1, token=1),
                )
        assert rows(db) == before
        assert await adapter.read_result_reference(run_id=attempt.run_id, result_version=1) is None

    asyncio.run(scenario())


def test_heartbeat_removes_stale_expiry_candidate_and_never_shortens_lease(db: Any) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        attempt = await claim(adapter, record)
        stale = (await adapter.expired_leases(now=NOW + 120, limit=5))[0]
        state = await adapter.heartbeat(attempt, now=NOW + 10, lease_seconds=200)
        assert state.lease_expires_at == NOW + 210
        shorter = await adapter.heartbeat(attempt, now=NOW + 11, lease_seconds=1)
        assert shorter.lease_expires_at == NOW + 210
        with pytest.raises(ConditionFailed):
            await adapter.expire_lease(stale, now=NOW + 300)
        current = await adapter.expired_leases(now=NOW + 210, limit=5)
        assert len(current) == 1 and current[0].lease_expires_at == NOW + 210
        reclaimed = await store(client()).expire_lease(current[0], now=NOW + 210)
        assert reclaimed.lease_takeover_count == 1 and reclaimed.attempt_count == 0
        history = decode(next(row for row in rows(db) if row["kind"]["S"] == "attempt"), AttemptRow)
        assert history.outcome == "lease_expired" and history.closed_at == NOW + 210

    asyncio.run(scenario())


def test_atomic_create_rolls_back_all_entities_on_global_run_collision(db: Any) -> None:
    async def scenario() -> None:
        first = await create(store(db))
        before = rows(db)
        with pytest.raises(ConditionFailed):
            await store(client()).create_job(
                principal(),
                submission(key="different-key"),
                job_id=uuid4(),
                run_id=first.current_run.run_id,
                now=NOW,
            )
        assert rows(db) == before

    asyncio.run(scenario())


def test_failed_publication_rolls_back_job_run_and_attempt(db: Any) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        attempt = await claim(adapter, record)
        # A preexisting append-only slot must abort the whole write, including attempt close.
        db.put_item(
            TableName=TABLE,
            Item=marshal(
                {
                    "pk": f"RUN#{attempt.run_id}",
                    "sk": "RESULT#00000000000000000001",
                    "synthetic_collision": True,
                }
            ),
        )
        before = rows(db)
        with pytest.raises(ConditionFailed):
            await adapter.finish(
                attempt,
                event=JobEvent.PUBLISH_RESULT,
                now=NOW + 1,
                result=result_reference(attempt.run_id, version=1, token=1),
            )
        assert rows(db) == before
        assert (await adapter.read_job(job_id=record.job_id)).status == JobStatus.RUNNING

    asyncio.run(scenario())


def test_late_old_run_dispatch_does_not_dispatch_resumed_run(db: Any) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        original = (await adapter.pending_dispatches(now=NOW, limit=10))[0]
        attempt = await claim(adapter, record)
        await adapter.finish(
            attempt, event=JobEvent.NEEDS_HUMAN, open_task_ids=(uuid4(),), now=NOW + 1
        )
        resumed = await adapter.resume_after_human(
            job_id=record.job_id,
            run=RunReference(
                run_id=uuid4(), revision=submission(revision_id="revision-two").revision
            ),
            now=NOW + 2,
        )
        marked = await store(client()).mark_dispatched(original, now=NOW + 3)
        assert marked.status == JobStatus.QUEUED
        assert marked.current_run == resumed.current_run
        old = db.get_item(
            TableName=TABLE, Key=marshal({"pk": f"RUN#{attempt.run_id}", "sk": "META"})
        )["Item"]
        assert decode(old, RunRow).status == JobStatus.WAITING_FOR_HUMAN
        assert (await claim(store(client()), resumed, now=NOW + 4)).fencing_token == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field", ["owner", "attempt_id", "run_id", "fencing_token", "expected_result_version"]
)
def test_forged_attempt_component_cannot_mutate(db: Any, field: str) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        attempt = await claim(adapter, record)
        forged = replace(
            attempt,
            **{field: 8 if field in {"fencing_token", "expected_result_version"} else uuid4()},
        )
        before = rows(db)
        with pytest.raises(ConditionFailed):
            await adapter.heartbeat(forged, now=NOW + 1, lease_seconds=120)
        with pytest.raises(ConditionFailed):
            await adapter.finish(
                forged,
                event=JobEvent.PERMANENT_ERROR,
                now=NOW + 1,
                problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
            )
        assert rows(db) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["retry", "takeover"])
def test_exhaustion_is_durable_and_releases_recovery_membership(db: Any, mode: str) -> None:
    async def scenario() -> None:
        adapter = store(db, policy=JobPolicy(max_attempts=1, max_lease_takeovers=1))
        record = await create(adapter)
        pending = (await adapter.pending_dispatches(now=NOW, limit=5))[0]
        await adapter.mark_dispatched(pending, now=NOW)
        attempt = await claim(adapter, record)
        if mode == "retry":
            final = await adapter.finish(
                attempt,
                event=JobEvent.RETRYABLE_ERROR,
                now=NOW + 1,
                problem=ServiceProblem(code=ServiceErrorCode.EXECUTION),
            )
            assert final.attempt_count == 1 and final.lease_takeover_count == 0
        else:
            candidate = (await adapter.expired_leases(now=NOW + 120, limit=5))[0]
            final = await adapter.expire_lease(candidate, now=NOW + 120)
            assert final.attempt_count == 0 and final.lease_takeover_count == 1
        assert final.status == JobStatus.FAILED and final.problem is not None
        assert await store(client()).read_job(job_id=record.job_id) == final
        assert await adapter.expired_leases(now=NOW + 1000, limit=5) == ()
        assert await adapter.stranded_retryables(stranded_before=NOW + 1000, limit=5) == ()
        assert await adapter.pending_dispatches(now=NOW + 1000, limit=5) == ()

    asyncio.run(scenario())


def test_query_pagination_is_bounded_and_uses_no_production_scan(db: Any) -> None:
    async def scenario() -> None:
        adapter = store(db)
        for index in range(105):
            await create(adapter, key=f"request-{index}")
        observed: list[str] = []

        def observe(params: Any, model: Any, **kwargs: Any) -> None:
            observed.append(model.name)
            if model.name == "Query":
                assert params["IndexName"] == "job-recovery"
                assert params["Limit"] <= 100
                assert "ConsistentRead" not in params
            if model.name == "GetItem":
                assert params["ConsistentRead"] is True

        db.meta.events.register("before-parameter-build.dynamodb", observe)
        try:
            due = await adapter.pending_dispatches(now=NOW, limit=103)
        finally:
            db.meta.events.unregister("before-parameter-build.dynamodb", observe)
        assert len(due) == 103 and len({item.job_id for item in due}) == 103
        assert observed.count("Query") == 2 and "Scan" not in observed
        assert len(await adapter.pending_dispatches(now=NOW, limit=2)) == 2
        for invalid in (0, -1, 1001):
            with pytest.raises(ValueError):
                await adapter.pending_dispatches(now=NOW, limit=invalid)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "code,reason,retryable",
    [
        ("ProvisionedThroughputExceededException", "throttled", True),
        ("ThrottlingException", "throttled", True),
        ("AccessDeniedException", "denied", False),
        ("ResourceNotFoundException", "invalid", False),
        ("InternalServerError", "unavailable", True),
    ],
)
def test_sdk_errors_are_finite_and_do_not_expose_diagnostics(
    code: str, reason: str, retryable: bool
) -> None:
    sdk = client()
    identity = uuid4()
    with Stubber(sdk) as stub:
        stub.add_client_error(
            "get_item",
            service_error_code=code,
            service_message="private-source-canary",
            expected_params={
                "TableName": TABLE,
                "Key": marshal({"pk": f"JOB#{identity}", "sk": "META"}),
                "ConsistentRead": True,
            },
        )
        with pytest.raises(DynamoDBJobStoreError) as caught:
            asyncio.run(store(sdk).read_job(job_id=identity))
        assert caught.value.reason == reason and caught.value.retryable is retryable
        assert str(caught.value) == "capability_unavailable"
        assert "private-source-canary" not in caught.value.problem.model_dump_json()
        assert caught.value.__suppress_context__ is True
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "reason,conditional",
    [
        ("ConditionalCheckFailed", True),
        ("TransactionConflict", False),
        ("ThrottlingError", False),
        ("ProvisionedThroughputExceeded", False),
    ],
)
def test_transaction_cancellation_reasons_distinguish_races_from_throttling(
    db: Any, reason: str, conditional: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        before = rows(db)
        calls = 0

        def fail(**kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise ClientError(
                {
                    "Error": {
                        "Code": "TransactionCanceledException",
                        "Message": "private-source-canary",
                    },
                    "CancellationReasons": [
                        {
                            "Code": reason,
                            "Message": "private-source-canary",
                            "Item": {"secret": {"S": "private-source-canary"}},
                        }
                    ],
                },
                "TransactWriteItems",
            )

        monkeypatch.setattr(db, "transact_write_items", fail)
        with pytest.raises(ConditionFailed if conditional else DynamoDBJobStoreError) as caught:
            await claim(adapter, record)
        assert "private-source-canary" not in str(caught.value)
        assert calls == 1 and rows(db) == before
        if not conditional:
            assert caught.value.retryable is True

    asyncio.run(scenario())


def test_committed_admission_with_lost_response_resolves_by_idempotency(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = db.transact_write_items
    calls = 0

    def lost_response(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        original(**kwargs)
        raise ReadTimeoutError(endpoint_url="https://private-source-canary.invalid")

    monkeypatch.setattr(db, "transact_write_items", lost_response)

    async def scenario() -> None:
        record, created = await store(db).create_job(
            principal(),
            submission(),
            job_id=uuid4(),
            run_id=uuid4(),
            now=NOW,
        )
        assert created is False and record.status == JobStatus.QUEUED
        assert calls == 1 and len(rows(db)) == 4
        assert len(await store(client()).pending_dispatches(now=NOW, limit=10)) == 1

    asyncio.run(scenario())


def test_invalid_problem_cannot_cross_persistence_boundary(db: Any) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        attempt = await claim(adapter, record)
        before = rows(db)
        problem = ServiceProblem(code=ServiceErrorCode.EXECUTION).model_copy(
            update={"message": "private-source-canary"}
        )
        with pytest.raises(ServiceFault) as caught:
            await adapter.finish(
                attempt, event=JobEvent.PERMANENT_ERROR, problem=problem, now=NOW + 1
            )
        assert caught.value.problem.code == ServiceErrorCode.VALIDATION
        assert "private-source-canary" not in str(caught.value)
        assert rows(db) == before

    asyncio.run(scenario())


def test_corrupt_unknown_schema_fails_with_finite_error(db: Any) -> None:
    async def scenario() -> None:
        record = await create(store(db))
        row = next(row for row in rows(db) if row["kind"]["S"] == "job")
        row["storage_schema"] = {"S": "private-source-canary"}
        db.put_item(TableName=TABLE, Item=row)
        with pytest.raises(DynamoDBJobStoreError) as caught:
            await store(client()).read_job(job_id=record.job_id)
        assert caught.value.reason == "corrupt" and caught.value.retryable is False
        assert "private-source-canary" not in str(caught.value)

    asyncio.run(scenario())


def test_serialization_validates_identity_and_unknown_fields(db: Any) -> None:
    async def scenario() -> None:
        await create(store(db))
        raw = next(row for row in rows(db) if row["kind"]["S"] == "job")
        decoded = decode(raw, JobRow)
        assert encode(decoded) == raw
        raw["private_text"] = {"S": "private-source-canary"}
        db.put_item(TableName=TABLE, Item=raw)
        with pytest.raises(DynamoDBJobStoreError):
            await store(db).read_job(job_id=decoded.job_id)

    asyncio.run(scenario())


def test_submission_loader_pins_metadata_and_new_revision_digest(db: Any) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        restored = await store(client()).read_submission(run_id=record.current_run.run_id)
        assert restored is not None
        assert restored.revision == submission().revision
        assert restored.documents == submission().documents
        assert restored.idempotency_key == f"run-{record.current_run.run_id}"
        assert submission_digest(restored) == submission_digest(submission())
        assert await adapter.read_submission(run_id=uuid4()) is None
        attempt = await claim(adapter, record)
        await adapter.finish(
            attempt,
            event=JobEvent.NEEDS_HUMAN,
            open_task_ids=(uuid4(),),
            now=NOW + 1,
        )
        next_run = RunReference(
            run_id=uuid4(),
            revision=submission(revision_id="revised-material").revision,
        )
        await adapter.resume_after_human(job_id=record.job_id, run=next_run, now=NOW + 2)
        revised = await store(client()).read_submission(run_id=next_run.run_id)
        assert revised is not None and revised.revision == next_run.revision
        assert revised.documents == restored.documents
        assert submission_digest(revised) != submission_digest(restored)
        assert await store(client()).read_submission(run_id=record.current_run.run_id) == restored

    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", ["digest", "revision", "document"])
def test_submission_loader_rejects_inconsistent_metadata(db: Any, tamper: str) -> None:
    async def scenario() -> None:
        record = await create(store(db))
        raw = next(row for row in rows(db) if row["kind"]["S"] == "run")
        if tamper == "digest":
            raw["payload_digest"] = {"S": "e" * 64}
        elif tamper == "revision":
            raw["revision"]["M"]["revision_id"] = {"S": "unexpected-revision"}
        else:
            raw["documents"]["L"][0]["M"]["case_id"] = {"S": "unexpected-case"}
        db.put_item(TableName=TABLE, Item=raw)
        with pytest.raises(DynamoDBJobStoreError) as caught:
            await store(client()).read_submission(run_id=record.current_run.run_id)
        assert caught.value.reason == "corrupt"

    asyncio.run(scenario())


def test_takeover_between_publication_read_and_commit_fences_every_write(
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stale_store = store(db)
        record = await create(stale_store)
        stale = await claim(stale_store, record)
        winner = store(client())
        original = stale_store._write

        async def race(changes: Any) -> None:
            lease = (await winner.expired_leases(now=NOW + 120, limit=5))[0]
            await winner.expire_lease(lease, now=NOW + 120)
            current = await claim(winner, record, now=NOW + 121)
            await winner.finish(
                current,
                event=JobEvent.PUBLISH_RESULT,
                now=NOW + 122,
                result=result_reference(current.run_id, version=1, token=current.fencing_token),
            )
            await original(changes)

        monkeypatch.setattr(stale_store, "_write", race)
        with pytest.raises(ConditionFailed):
            await stale_store.finish(
                stale,
                event=JobEvent.PUBLISH_RESULT,
                now=NOW + 119,
                result=result_reference(stale.run_id, version=1, token=1),
            )
        reference = await winner.read_result_reference(run_id=stale.run_id, result_version=1)
        assert reference is not None and reference.fencing_token == 2
        history = [decode(row, AttemptRow) for row in rows(db) if row["kind"]["S"] == "attempt"]
        assert {(row.fencing_token, row.outcome) for row in history} == {
            (1, "lease_expired"),
            (2, "publish_result"),
        }

    asyncio.run(scenario())


def test_heartbeat_between_expiry_read_and_commit_prevents_takeover(
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        attempt = await claim(adapter, record)
        candidate = (await adapter.expired_leases(now=NOW + 120, limit=5))[0]
        original = adapter._write

        async def race(changes: Any) -> None:
            await store(client()).heartbeat(attempt, now=NOW + 119, lease_seconds=120)
            await original(changes)

        monkeypatch.setattr(adapter, "_write", race)
        with pytest.raises(ConditionFailed):
            await adapter.expire_lease(candidate, now=NOW + 120)
        state = await store(client()).read_job(job_id=record.job_id)
        assert state is not None and state.status == JobStatus.RUNNING
        assert state.lease_takeover_count == 0
        assert await store(client()).expired_leases(now=NOW + 120, limit=5) == ()
        assert len(await store(client()).pending_dispatches(now=NOW + 120, limit=5)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["mark", "reschedule"])
def test_stale_dispatch_schedule_cannot_overwrite_new_schedule(db: Any, operation: str) -> None:
    async def scenario() -> None:
        adapter = store(db)
        await create(adapter)
        stale = (await adapter.pending_dispatches(now=NOW, limit=5))[0]
        assert await adapter.reschedule_dispatch(stale, available_at=NOW + 50)
        before = rows(db)
        with pytest.raises(ConditionFailed):
            if operation == "mark":
                await adapter.mark_dispatched(stale, now=NOW + 1)
            else:
                await adapter.reschedule_dispatch(stale, available_at=NOW + 100)
        assert rows(db) == before
        current = (await adapter.pending_dispatches(now=NOW + 50, limit=5))[0]
        assert current.available_at == NOW + 50 and current.dispatch_attempts == 1

    asyncio.run(scenario())


def test_stale_secondary_index_candidate_is_rechecked_against_base_table(
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        adapter = store(db)
        await create(adapter)
        original = adapter._candidates

        async def stale(partition: str, cutoff: int, limit: int) -> Any:
            candidates = await original(partition, cutoff, limit)
            other = store(client())
            record = (await other.pending_dispatches(now=NOW, limit=5))[0]
            await other.mark_dispatched(record, now=NOW)
            return candidates

        monkeypatch.setattr(adapter, "_candidates", stale)
        assert await adapter.pending_dispatches(now=NOW, limit=5) == ()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("ConditionalCheckFailed", ConditionFailed),
        ("ThrottlingError", DynamoDBJobStoreError),
        ("ValidationError", DynamoDBJobStoreError),
    ],
)
def test_sdk_transaction_failure_validates_real_request_shape(
    db: Any, reason: str, expected: Any
) -> None:
    async def scenario() -> None:
        record = await create(store(db))
        job = next(row for row in rows(db) if row["kind"]["S"] == "job")
        run = next(row for row in rows(db) if row["kind"]["S"] == "run")
        sdk = client()
        observed = []

        def inspect(params: Any, **kwargs: Any) -> None:
            observed.append(params)
            transactions = params["TransactItems"]
            assert len(transactions) == 3
            assert len(params["ClientRequestToken"]) == 36
            for entry in transactions:
                put = entry["Put"]
                assert put["TableName"] == TABLE
                assert "ConditionExpression" in put
                assert "ReturnValuesOnConditionCheckFailure" not in put
            run_put = next(
                entry["Put"] for entry in transactions if entry["Put"]["Item"]["kind"]["S"] == "run"
            )
            assert {"version", "lease_expires_at"} <= set(
                run_put["ExpressionAttributeNames"].values()
            )
            assert run_put["Item"]["fencing_token"] == {"N": "1"}

        sdk.meta.events.register("before-parameter-build.dynamodb.TransactWriteItems", inspect)
        with Stubber(sdk) as stub:
            stub.add_response(
                "get_item",
                {"Item": job},
                {
                    "TableName": TABLE,
                    "Key": marshal({"pk": f"JOB#{record.job_id}", "sk": "META"}),
                    "ConsistentRead": True,
                },
            )
            stub.add_response(
                "transact_get_items",
                {"Responses": [{"Item": job}, {"Item": run}]},
                {
                    "TransactItems": [
                        {
                            "Get": {
                                "TableName": TABLE,
                                "Key": {key: item[key] for key in ("pk", "sk")},
                            }
                        }
                        for item in (job, run)
                    ],
                },
            )
            stub.add_client_error(
                "transact_write_items",
                service_error_code="TransactionCanceledException",
                service_message="private-source-canary",
                modeled_fields={
                    "CancellationReasons": [{"Code": reason, "Message": "private-source-canary"}]
                },
                expected_params={"TransactItems": ANY, "ClientRequestToken": ANY},
            )
            with pytest.raises(expected) as caught:
                await claim(store(sdk), record)
            assert "private-source-canary" not in str(caught.value)
            if reason == "ValidationError":
                assert caught.value.reason == "invalid" and not caught.value.retryable
            stub.assert_no_pending_responses()
        assert len(observed) == 1
        assert len(rows(db)) == 4

    asyncio.run(scenario())


def test_admission_rejects_nonopaque_principal_and_excessive_metadata(db: Any) -> None:
    async def scenario() -> None:
        adapter = store(db)
        ordinary = submission()
        many = ordinary.model_copy(
            update={
                "documents": tuple(
                    ordinary.documents[0].model_copy(update={"document_id": f"doc-{index}"})
                    for index in range(65)
                ),
            }
        )
        for caller, request in (
            (principal("private-source-canary/filename.pdf"), ordinary),
            (principal(), many),
        ):
            with pytest.raises(ServiceFault) as caught:
                await adapter.create_job(caller, request, job_id=uuid4(), run_id=uuid4(), now=NOW)
            assert caught.value.problem.code == ServiceErrorCode.VALIDATION
            assert "private-source-canary" not in str(caught.value)
        assert rows(db) == []

    asyncio.run(scenario())


def test_resume_replay_does_not_create_another_run_or_outbox(db: Any) -> None:
    async def scenario() -> None:
        adapter = store(db)
        record = await create(adapter)
        attempt = await claim(adapter, record)
        await adapter.finish(
            attempt,
            event=JobEvent.NEEDS_HUMAN,
            open_task_ids=(uuid4(),),
            now=NOW + 1,
        )
        next_run = RunReference(
            run_id=uuid4(), revision=submission(revision_id="next-revision").revision
        )
        await adapter.resume_after_human(job_id=record.job_id, run=next_run, now=NOW + 2)
        before = rows(db)
        with pytest.raises(ConditionFailed):
            await store(client()).resume_after_human(
                job_id=record.job_id, run=next_run, now=NOW + 3
            )
        assert rows(db) == before
        assert len(await adapter.pending_dispatches(now=NOW + 3, limit=10)) == 2

    asyncio.run(scenario())
