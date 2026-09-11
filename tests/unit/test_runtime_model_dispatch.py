"""Real Runtime, durable job/run stores and SDK transport cannot send after revocation."""

import asyncio
import runpy
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.job_store import InMemoryResultStore
from appraisal_review.adapters.local.sqlite_model_dispatch import SqliteModelDispatchStore
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.application.outbox import DispatchMessage
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.runtime_worker import RuntimeWorker
from appraisal_review.application.service_guards import Principal

_ROOT = Path(__file__).resolve().parents[2]
_workflow = runpy.run_path(str(_ROOT / "tests/integration/test_integrated_workflow.py"))
_dispatch = runpy.run_path(str(_ROOT / "tests/unit/test_model_dispatch.py"))
NOW, Harness = _workflow["NOW"], _workflow["Harness"]
client, server = _dispatch["client"], _dispatch["server"]


@pytest.mark.parametrize("reason", ["cancel", "fence", "principal", "lease"])
def test_runtime_durable_revocation_during_dispatch_wait_prevents_physical_send(reason, tmp_path):
    acquired = threading.Event()

    class ObservedStore(SqliteModelDispatchStore):
        def try_acquire(self, scope, owner):
            result = super().try_acquire(scope, owner)
            if result:
                acquired.set()
            return result

    async def scenario(sdk, sends):
        h = await Harness.create(tmp_path)
        h.client = sdk
        clock = [NOW]
        service = ReviewJobService(h.store, InMemoryResultStore(), clock=lambda: clock[0])
        queued = (await service.due_dispatches())[0]
        message = DispatchMessage(
            job_id=h.job_id,
            run_id=h.run_id,
            outbox_seq=queued.outbox_seq,
            dispatch_token=queued.dispatch_token,
            enqueued_at=NOW,
        )
        worker = RuntimeWorker(service, h.execution(), timeout_seconds=8)
        pending = asyncio.create_task(worker.process(message))
        assert await asyncio.to_thread(acquired.wait, 5)
        before = await h.store.read_job(job_id=h.job_id)
        if reason == "cancel":
            await service.cancel(h.principal, h.job_id)
        elif reason == "fence":
            leases = await h.store.expired_leases(now=NOW + 121, limit=10)
            assert leases
            await h.store.expire_lease(leases[0], now=NOW + 121)
            replacement = await h.store.claim(
                job_id=h.job_id, run_id=h.run_id, owner=uuid4(), lease_seconds=60, now=NOW + 121
            )
            assert replacement.fencing_token == 2
        elif reason == "principal":
            h.principal = Principal(
                actor=h.principal.actor,
                case_ids=h.principal.case_ids,
                permissions=frozenset(),
            )
        else:
            clock[0] = NOW + 121
        revoked_at = time.monotonic()
        await pending
        await asyncio.sleep(1.2)
        assert not [sent for sent, _ in sends if sent > revoked_at]
        assert sends == []
        assert h.prepared == []
        failures = await h.trace.read_failures(before.current_run.run_id)
        assert len(failures) == 1
        assert (
            failures[0].budget_after.model_calls_remaining
            < failures[0].budget_before.model_calls_remaining
        )

    with server() as (url, sends):
        sdk = client(url, SharedModelDispatcher(ObservedStore(tmp_path / "dispatch.sqlite3")))
        try:
            asyncio.run(scenario(sdk, sends))
        finally:
            sdk.close()


def test_execution_scope_revokes_lingering_workers_and_restores_outer_authority():
    from appraisal_review.ports.model_dispatch import (
        dispatch_async_authority,
        dispatch_authority,
        inherited_dispatch_authority,
    )

    async def scenario():
        calls = []

        async def current():
            calls.append("checked")

        with dispatch_authority(lambda: True):
            parent = inherited_dispatch_authority()
            with dispatch_async_authority(current):
                lingering = inherited_dispatch_authority()
                assert await asyncio.to_thread(lingering)
            assert not await asyncio.to_thread(lingering)
            assert inherited_dispatch_authority() is parent
            assert await asyncio.to_thread(parent)
        assert calls == ["checked"]

    asyncio.run(scenario())
