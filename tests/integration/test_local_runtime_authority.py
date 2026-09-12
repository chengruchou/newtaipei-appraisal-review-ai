"""Durable runtime authority, causal trace and HTTP isolation with real SQLite/C2."""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from appraisal_review.adapters.local.artifact_publication import CommittedResultResolver
from appraisal_review.adapters.local.integrated_service import (
    CurrentSourceExecution,
    LocalDirectory,
    LocalMaterialCatalog,
    create_integrated_service,
)
from appraisal_review.adapters.local.service import public_verification
from appraisal_review.adapters.local.sqlite_publication import (
    SQLiteArtifactObjectStore,
    SQLiteManifestRepository,
)
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.adapters.local.workflow_runtime import (
    SQLiteDecisionTrace,
    SQLiteExecutionAuthority,
    SQLiteWorkflowReviews,
)
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.application.document_review import document_adapters
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.runtime_worker import ExecutedReview
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.config import Settings
from appraisal_review.domain.factor_models import AgentReviewRun, WorkflowStatus
from appraisal_review.domain.service_contracts import (
    ActionProposal,
    ActorReference,
    Budget,
    BudgetConsumption,
    DecisionEvent,
    DeterministicReviewArguments,
    ExecutionStatus,
    HumanResponse,
    ResponseAction,
    ReviewSubmission,
    SelectionFailureEvent,
    ServiceResult,
)
from appraisal_review.ports.jobs import ConditionFailed
from tests.unit.test_human_task_service import fact_task


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    helper = importlib.import_module("integration_fixture")
    fixture = asyncio.run(helper.create_integration_fixture(tmp_path / "fixture"))
    time = SimpleNamespace(now=1000)
    store = SQLiteReviewStore(tmp_path / "private/review.sqlite3", clock=lambda: time.now)
    directory = LocalDirectory({"a" * 40: fixture.principal})
    catalog = LocalMaterialCatalog(store)
    catalog.register(fixture.principal, fixture.snapshot)
    job_id, run_id = uuid4(), uuid4()
    asyncio.run(
        store.create_job(
            fixture.principal,
            ReviewSubmission(
                revision=fixture.snapshot.revision.reference,
                documents=fixture.snapshot.revision.documents,
                idempotency_key="runtime-authority-test",
            ),
            job_id=job_id,
            run_id=run_id,
            now=time.now,
        )
    )
    dispatch = asyncio.run(store.pending_dispatches(now=time.now, limit=10))
    assert len(dispatch) == 1
    asyncio.run(store.mark_dispatched(dispatch[0], now=time.now))
    attempt = asyncio.run(
        store.claim(job_id=job_id, run_id=run_id, owner=uuid4(), lease_seconds=60, now=time.now)
    )
    record = asyncio.run(store.read_job(job_id=job_id))
    documents = fixture.documents
    authority = SQLiteExecutionAuthority(store, documents, directory)
    source_calls = []
    original_read = documents.read_snapshot

    def observed_read(principal, run, reference):
        source_calls.append((principal, run, reference))
        return original_read(principal, run, reference)

    monkeypatch.setattr(documents, "read_snapshot", observed_read)
    controller = build_controller(
        Settings(runtime_mode="local", synthetic_demo=False),
        adapters=document_adapters(
            fixture.configuration.inputs.parser(), fixture.snapshot.material, None
        ),
    )

    class ActualExecution:
        def __init__(self):
            self.calls = []
            self.after = None

        async def execute(self, current, claim):
            self.calls.append((current, claim))
            reviewed = await controller.review(fixture.request)
            if self.after is not None:
                await self.after()
            return ExecutedReview(
                result=ServiceResult(
                    run=current.current_run,
                    result_version=claim.expected_result_version + 1,
                    execution_status=ExecutionStatus.SUCCEEDED,
                    business_status=reviewed.status,
                    artifact_status=reviewed.artifact_status,
                    findings=tuple(reviewed.case_review.findings),
                    verification=public_verification(reviewed.verification),
                )
            )

    execution = ActualExecution()
    wrapper = CurrentSourceExecution(execution, directory, catalog, documents, store)

    def allow_sources(principal, run, sources):
        for source in sources:
            reference = catalog.source(source.document_id, source.version, source.content_hash)
            documents.read_snapshot(principal, run, reference)

    repo = SQLiteManifestRepository(store, source_authorizer=allow_sources, clock=lambda: time.now)
    resolver = CommittedResultResolver(manifests=repo, objects=SQLiteArtifactObjectStore(store))
    return SimpleNamespace(**locals())


def pin(scene):
    scene.documents.create_snapshot(
        scene.fixture.principal, scene.record.current_run, scene.fixture.snapshot.revision
    )


def task_for(scene):
    task = fact_task(scene.fixture.snapshot, scene.run_id)
    return task.model_copy(update={"run": scene.record.current_run})


def failure(run, *, parents=()):
    budget = Budget(steps_remaining=3, model_calls_remaining=0, retries_remaining=0)
    return SelectionFailureEvent(
        event_id=uuid4(),
        parent_event_ids=parents,
        run=run,
        snapshot_digest=run.revision.material_digest,
        policy_version="policy-v1",
        actor=ActorReference(actor_id="local-selector", kind="system"),
        error_code="no_selection",
        latency_ms=0,
        budget_before=budget,
        budget_after=budget,
        budget_consumed=BudgetConsumption(steps=0, model_calls=0, retries=0),
    )


def decision(scene, *, parents=()):
    budget = Budget(steps_remaining=3, model_calls_remaining=0, retries_remaining=0)
    run = scene.record.current_run
    proposal = ActionProposal(
        proposal_id=uuid4(),
        run=run,
        action_id="review-exact",
        action="deterministic_review",
        policy_version="policy-v1",
        snapshot_digest=run.revision.material_digest,
        proposer=ActorReference(actor_id="local-selector", kind="system"),
        arguments=DeterministicReviewArguments(
            revision=run.revision, rules=scene.fixture.snapshot.revision.rules
        ),
    )
    return DecisionEvent(
        event_id=uuid4(),
        parent_event_ids=parents,
        proposal=proposal,
        policy_version="policy-v1",
        state_before="material_ready",
        state_after="material_ready",
        disposition="rejected",
        reason_code="test-prerequisite",
        reviewer_summary="Not executed.",
        budget_before=budget,
        budget_after=budget,
        budget_consumed=BudgetConsumption(steps=0, model_calls=0, retries=0),
    )


def test_trace_exact_replay_causal_cross_kind_and_reopen(scene):
    trace = SQLiteDecisionTrace(scene.store)
    parent = failure(scene.record.current_run)
    child = decision(scene, parents=(parent.event_id,))
    asyncio.run(trace.append_failure(parent))
    asyncio.run(trace.append(child))
    asyncio.run(trace.append_failure(parent))
    asyncio.run(trace.append(child))
    reopened = SQLiteDecisionTrace(
        SQLiteReviewStore(scene.store.path, clock=lambda: scene.time.now)
    )
    assert asyncio.run(reopened.read(scene.run_id)) == (child,)
    assert asyncio.run(reopened.read_failures(scene.run_id)) == (parent,)
    with closing(sqlite3.connect(scene.store.path)) as connection, connection:
        assert connection.execute("SELECT count(*) FROM workflow_trace").fetchone()[0] == 2
    with pytest.raises(ServiceFault):
        asyncio.run(trace.append(child.model_copy(update={"reviewer_summary": "changed"})))
    assert asyncio.run(reopened.read(scene.run_id)) == (child,)


def test_trace_rejects_orphans_and_foreign_run_parents(scene):
    trace = SQLiteDecisionTrace(scene.store)
    parent = failure(scene.record.current_run)
    with pytest.raises(ServiceFault):
        asyncio.run(trace.append(decision(scene, parents=(parent.event_id,))))
    asyncio.run(trace.append_failure(parent))
    foreign = failure(
        scene.record.current_run.model_copy(update={"run_id": uuid4()}), parents=(parent.event_id,)
    )
    with pytest.raises(ServiceFault):
        asyncio.run(trace.append_failure(foreign))
    assert not asyncio.run(trace.read(foreign.run.run_id))


def test_trace_rejects_same_run_id_with_different_revision(scene):
    trace = SQLiteDecisionTrace(scene.store)
    first = failure(scene.record.current_run)
    asyncio.run(trace.append_failure(first))
    different = first.run.model_copy(
        update={"revision": first.run.revision.model_copy(update={"revision_id": str(uuid4())})}
    )
    with pytest.raises(ServiceFault):
        asyncio.run(trace.append_failure(failure(different, parents=(first.event_id,))))
    assert asyncio.run(trace.read_failures(scene.run_id)) == (first,)


@pytest.mark.parametrize("damage", ["run", "event_id", "orphan_parent"])
def test_trace_read_rejects_tampered_index_binding_and_causality(scene, damage):
    trace = SQLiteDecisionTrace(scene.store)
    event = failure(scene.record.current_run)
    asyncio.run(trace.append_failure(event))
    payload = event.model_dump(mode="json")
    if damage == "run":
        payload["run"]["run_id"] = str(uuid4())
    elif damage == "event_id":
        payload["event_id"] = str(uuid4())
    else:
        payload["parent_event_ids"] = [str(uuid4())]
    with closing(sqlite3.connect(scene.store.path)) as connection, connection:
        connection.execute("UPDATE workflow_trace SET payload=?", (json.dumps(payload),))
    with pytest.raises(ServiceFault):
        asyncio.run(SQLiteDecisionTrace(scene.store).read_failures(scene.run_id))


def test_reviews_exact_put_read_replay_run_binding_and_reopen(scene):
    reviews = SQLiteWorkflowReviews(scene.store)
    run = scene.record.current_run
    review = AgentReviewRun(case_id=scene.record.case_id, status=WorkflowStatus.NEEDS_REVIEW)
    assert asyncio.run(reviews.read(run)) is None
    asyncio.run(reviews.put(run, review))
    asyncio.run(reviews.put(run, review))
    reopened = SQLiteWorkflowReviews(SQLiteReviewStore(scene.store.path, clock=lambda: 1000))
    assert asyncio.run(reopened.read(run)) == review
    with pytest.raises(ServiceFault):
        asyncio.run(reviews.put(run, review.model_copy(update={"status": WorkflowStatus.FAILED})))
    with pytest.raises(ServiceFault):
        asyncio.run(reviews.read(run.model_copy(update={"attempt_id": uuid4()})))
    with pytest.raises(ServiceFault):
        asyncio.run(reviews.put(run, review.model_copy(update={"case_id": str(uuid4())})))


def test_reviews_read_rechecks_stored_case_binding(scene):
    reviews = SQLiteWorkflowReviews(scene.store)
    review = AgentReviewRun(case_id=scene.record.case_id, status=WorkflowStatus.NEEDS_REVIEW)
    asyncio.run(reviews.put(scene.record.current_run, review))
    changed = review.model_copy(update={"case_id": str(uuid4())})
    with closing(sqlite3.connect(scene.store.path)) as connection, connection:
        connection.execute("UPDATE workflow_reviews SET review=?", (changed.model_dump_json(),))
    with pytest.raises(ServiceFault):
        asyncio.run(reviews.read(scene.record.current_run))


def test_current_execution_creates_new_c2_snapshot_and_rechecks_after_actual_controller(scene):
    result = asyncio.run(scene.wrapper.execute(scene.record, scene.attempt))
    assert result.result.business_status == WorkflowStatus.NEEDS_REVIEW
    assert len(scene.execution.calls) == 1 and len(scene.source_calls) >= 6
    assert all(call[1] == scene.record.current_run for call in scene.source_calls)
    assert scene.run_id != scene.fixture.run.run_id
    assert asyncio.run(scene.store.read_snapshot(revision=scene.record.current_run.revision))
    assert not asyncio.run(scene.store.list_tasks(job_id=scene.job_id))


def test_resumed_human_response_run_gets_its_own_exact_c2_snapshot(scene):
    pin(scene)
    task = task_for(scene)
    asyncio.run(
        scene.authority.register(scene.record, scene.attempt, scene.fixture.snapshot, (task,))
    )
    asyncio.run(
        scene.store.finish(
            scene.attempt,
            event=JobEvent.NEEDS_HUMAN,
            now=scene.time.now,
            open_task_ids=(task.task_id,),
        )
    )
    service = HumanTaskService(
        scene.store, clock=lambda: scene.time.now, new_revision_id=lambda: str(uuid4())
    )
    command = HumanResponse(
        task_id=task.task_id,
        expected_version=task.version,
        revision=scene.record.current_run.revision,
        side_digest=task.side.input_digest,
        idempotency_key="trusted-answer",
        action=ResponseAction.CONFIRM,
    )
    asyncio.run(service.respond(scene.fixture.principal, task.task_id, command))
    resumed = asyncio.run(scene.store.read_job(job_id=scene.job_id))
    assert resumed.current_run.run_id != scene.run_id
    attempt = asyncio.run(
        scene.store.claim(
            job_id=scene.job_id,
            run_id=resumed.current_run.run_id,
            owner=uuid4(),
            lease_seconds=60,
            now=scene.time.now,
        )
    )
    resumed = asyncio.run(scene.store.read_job(job_id=scene.job_id))
    scene.source_calls.clear()
    result = asyncio.run(scene.wrapper.execute(resumed, attempt))
    assert result.result.run == resumed.current_run
    assert scene.source_calls and all(c[1] == resumed.current_run for c in scene.source_calls)
    for ref in (
        asyncio.run(scene.catalog.snapshot(scene.fixture.principal, resumed.current_run.revision))
    ).revision.documents:
        assert scene.documents.read_snapshot(
            scene.fixture.principal, resumed.current_run, ref
        ).content


@pytest.mark.parametrize("change", ["lease", "cancel", "directory", "source_grant"])
def test_registration_rejects_lost_authority_without_persisting_tasks(scene, change):
    pin(scene)
    task = task_for(scene)
    if change == "lease":
        scene.time.now += 60
    elif change == "cancel":
        asyncio.run(scene.store.cancel(job_id=scene.job_id, now=scene.time.now))
    elif change == "directory":
        scene.directory.revoke(
            scene.fixture.principal.actor.actor_id, lambda: scene.repo.revoke(scene.record.case_id)
        )
    else:
        scene.documents.authorization.grants = ()
    with pytest.raises((ServiceFault, ConditionFailed)):
        asyncio.run(
            scene.authority.register(scene.record, scene.attempt, scene.fixture.snapshot, (task,))
        )
    assert not asyncio.run(scene.store.list_tasks(job_id=scene.job_id))


def test_registration_rejects_live_claim_for_another_job(scene):
    pin(scene)
    other_job, other_run = uuid4(), uuid4()
    asyncio.run(
        scene.store.create_job(
            scene.fixture.principal,
            ReviewSubmission(
                revision=scene.fixture.snapshot.revision.reference,
                documents=scene.fixture.snapshot.revision.documents,
                idempotency_key="other-job",
            ),
            job_id=other_job,
            run_id=other_run,
            now=1000,
        )
    )
    other = asyncio.run(
        scene.store.claim(
            job_id=other_job, run_id=other_run, owner=uuid4(), lease_seconds=300, now=1000
        )
    )
    scene.time.now = 1060  # Original attempt expired; unrelated claim remains live.
    with pytest.raises((ServiceFault, ConditionFailed)):
        asyncio.run(
            scene.authority.register(
                scene.record, other, scene.fixture.snapshot, (task_for(scene),)
            )
        )
    assert not asyncio.run(scene.store.list_tasks(job_id=scene.job_id))


def test_registration_rechecks_durable_revocation_after_final_c2_read(scene, monkeypatch):
    pin(scene)
    read = scene.documents.read_snapshot
    count = 0

    def revoke_after_read(principal, run, reference):
        nonlocal count
        actual = read(principal, run, reference)
        count += 1
        if count == len(scene.fixture.snapshot.revision.documents):
            scene.directory.revoke(
                principal.actor.actor_id, lambda: scene.repo.revoke(scene.record.case_id)
            )
            scene.documents.authorization.grants = ()
        return actual

    monkeypatch.setattr(scene.documents, "read_snapshot", revoke_after_read)
    with pytest.raises((ServiceFault, ConditionFailed)):
        asyncio.run(
            scene.authority.register(
                scene.record, scene.attempt, scene.fixture.snapshot, (task_for(scene),)
            )
        )
    assert not asyncio.run(scene.store.list_tasks(job_id=scene.job_id))


@pytest.mark.parametrize("change", ["cancel", "lease", "directory", "source_grant"])
def test_execution_rechecks_authority_after_controller(scene, change):
    async def after():
        if change == "cancel":
            await scene.store.cancel(job_id=scene.job_id, now=scene.time.now)
        elif change == "lease":
            scene.time.now += 60
        elif change == "directory":
            scene.directory.revoke(
                scene.fixture.principal.actor.actor_id,
                lambda: scene.repo.revoke(scene.record.case_id),
            )
        else:
            scene.documents.authorization.grants = ()

    scene.execution.after = after
    with pytest.raises((ServiceFault, ConditionFailed)):
        asyncio.run(scene.wrapper.execute(scene.record, scene.attempt))
    assert len(scene.execution.calls) == 1


def test_http_actual_source_bytes_and_denials_never_expose_paths_or_foreign_jobs(scene):
    foreign = replace(
        scene.fixture.principal, actor=ActorReference(actor_id=str(uuid4()), kind="human")
    )
    other_case = replace(foreign, case_ids=frozenset({str(uuid4())}))
    scene.directory._sessions.update({"b" * 40: foreign, "c" * 40: other_case})
    app = create_integrated_service(
        authority="127.0.0.1:8765",
        directory=scene.directory,
        catalog=scene.catalog,
        documents=scene.documents,
        execution=scene.execution,
        resolver=scene.resolver,
        worker_enabled=False,
    )
    ref = scene.fixture.snapshot.revision.documents[0]
    url = f"/v1/documents/{ref.document_id}/content"
    query = {"version": ref.version, "content_hash": ref.content_hash}
    headers = {"Authorization": "Bearer " + "a" * 40}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get(url, params=query, headers=headers)
        assert response.status_code == 200
        assert response.content == scene.documents.read(scene.fixture.principal, ref).content
        assert response.headers["cache-control"] == "no-store"
        denied = [client.get(url, params=query)]
        denied.extend(
            client.get(url, params=query, headers={"Authorization": "Bearer " + token * 40})
            for token in ("b", "c")
        )
        denied.extend(
            client.get(url, params=query | changed, headers=headers)
            for changed in (
                {"version": str(uuid4())},
                {"content_hash": "0" * 64},
                {"version": "file:///private/hidden.pdf"},
            )
        )
        for token in ("b", "c"):
            foreign_headers = {"Authorization": "Bearer " + token * 40}
            for suffix in ("", "/result", f"/artifacts/{uuid4()}/content"):
                denied.append(
                    client.get(f"/v1/review-jobs/{scene.job_id}{suffix}", headers=foreign_headers)
                )
        denied.append(client.get(url, params=query, headers=headers | {"host": "foreign:8765"}))
        for response in denied:
            assert response.status_code in {403, 404, 409, 422}, response.text
            assert not response.content.startswith(b"%PDF")
            assert (
                "file:" not in response.text and str(scene.fixture.directory) not in response.text
            )
            assert "source_uri" not in response.text and "document_uri" not in response.text
            assert ref.content_hash not in response.text


def dispatch_after_crash(scene, *, before_ack):
    import subprocess
    import sys

    from appraisal_review.adapters.local.integrated_service import SQLiteDispatchQueue

    job_id, run_id = uuid4(), uuid4()
    asyncio.run(
        scene.store.create_job(
            scene.fixture.principal,
            ReviewSubmission(
                revision=scene.fixture.snapshot.revision.reference,
                documents=scene.fixture.snapshot.revision.documents,
                idempotency_key="queue-crash",
            ),
            job_id=job_id,
            run_id=run_id,
            now=1000,
        )
    )
    code = """
import asyncio, os, sys
from pathlib import Path
from appraisal_review.adapters.local.integrated_service import SQLiteDispatchQueue
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.outbox import OutboxDispatcher
from appraisal_review.application.review_jobs import ReviewJobService
store = SQLiteReviewStore(Path(sys.argv[1]), clock=lambda: 1000)
queue = SQLiteDispatchQueue(store)
service = ReviewJobService(store, store.results, clock=lambda: 1000)
async def send(message):
    await queue.send(message)
    if sys.argv[2] == "before_ack":
        os._exit(82)
result = asyncio.run(OutboxDispatcher(service, send).run_once())
assert result.sent == 1
os._exit(81)
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(scene.store.path),
            "before_ack" if before_ack else "after_ack",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert child.returncode == (82 if before_ack else 81), child.stderr
    reopened = SQLiteReviewStore(scene.store.path, clock=lambda: 1001)
    queue = SQLiteDispatchQueue(reopened)
    pending = queue.pending()
    assert len(pending) == 1
    assert (pending[0].job_id, pending[0].run_id) == (job_id, run_id)
    return reopened, queue, pending[0]


def test_queue_handoff_survives_process_exit_after_outbox_ack_and_processes_actual_review(scene):
    from appraisal_review.adapters.local.integrated_service import SQLiteDispatchQueue
    from appraisal_review.application.review_jobs import ReviewJobService
    from appraisal_review.application.runtime_worker import RuntimeWorker

    reopened, queue, message = dispatch_after_crash(scene, before_ack=False)
    service = ReviewJobService(reopened, reopened.results, clock=lambda: 1001)
    assert not asyncio.run(service.due_dispatches())
    assert queue.pending() == (message,)
    catalog = LocalMaterialCatalog(reopened)
    wrapper = CurrentSourceExecution(
        scene.execution, scene.directory, catalog, scene.documents, reopened
    )
    result = asyncio.run(RuntimeWorker(service, wrapper).process(message))
    assert result == "published"
    # Worker completion precedes queue acknowledgement; a duplicate delivery is harmless.
    assert SQLiteDispatchQueue(reopened).pending() == (message,)
    assert asyncio.run(RuntimeWorker(service, wrapper).process(message)) == "superseded"
    queue.acknowledge(message)
    assert not SQLiteDispatchQueue(SQLiteReviewStore(scene.store.path)).pending()
    stored = asyncio.run(service.result(scene.fixture.principal, message.job_id))
    assert stored.business_status == WorkflowStatus.NEEDS_REVIEW and stored.durable
    assert len(scene.execution.calls) == 1


def test_queue_retry_after_send_before_ack_crash_preserves_first_handoff_timestamp(scene):
    from appraisal_review.application.outbox import OutboxDispatcher
    from appraisal_review.application.review_jobs import ReviewJobService

    reopened, queue, original = dispatch_after_crash(scene, before_ack=True)
    service = ReviewJobService(reopened, reopened.results, clock=lambda: 1001)
    assert len(asyncio.run(service.due_dispatches())) == 1
    outcome = asyncio.run(OutboxDispatcher(service, queue.send).run_once())
    assert outcome.sent == 1
    assert queue.pending() == (original,)
    assert not asyncio.run(service.due_dispatches())


def test_queue_duplicate_replay_different_payload_and_stored_token_binding(scene):
    from appraisal_review.adapters.local.integrated_service import SQLiteDispatchQueue
    from appraisal_review.application.outbox import DispatchMessage

    queue = SQLiteDispatchQueue(scene.store)
    message = DispatchMessage(scene.job_id, scene.run_id, 1, uuid4(), 1000)
    asyncio.run(queue.send(message))
    asyncio.run(queue.send(message))
    assert queue.pending() == (message,)
    with pytest.raises(ServiceFault):
        asyncio.run(queue.send(replace(message, job_id=uuid4())))
    with closing(sqlite3.connect(scene.store.path)) as connection, connection:
        payload = json.loads(
            connection.execute("SELECT payload FROM local_dispatch_queue").fetchone()[0]
        )
        payload["dispatch_token"] = str(uuid4())
        connection.execute("UPDATE local_dispatch_queue SET payload=?", (json.dumps(payload),))
    with pytest.raises(ServiceFault):
        queue.pending()


@pytest.mark.parametrize("change", ["lease", "cancel", "directory", "source_grant"])
def test_initial_execution_blocks_before_controller_when_authority_is_lost(scene, change):
    if change == "lease":
        scene.time.now += 60
    elif change == "cancel":
        asyncio.run(scene.store.cancel(job_id=scene.job_id, now=scene.time.now))
    elif change == "directory":
        scene.directory.revoke(
            scene.fixture.principal.actor.actor_id, lambda: scene.repo.revoke(scene.record.case_id)
        )
    else:
        scene.documents.authorization.grants = ()
    with pytest.raises((ServiceFault, ConditionFailed)):
        asyncio.run(scene.wrapper.execute(scene.record, scene.attempt))
    assert not scene.execution.calls
    assert not asyncio.run(scene.store.list_tasks(job_id=scene.job_id))


def test_concurrent_trace_replay_across_independent_connections_is_one_event(scene):
    first = SQLiteDecisionTrace(scene.store)
    second = SQLiteDecisionTrace(SQLiteReviewStore(scene.store.path))
    event = failure(scene.record.current_run)

    async def race():
        await asyncio.gather(first.append_failure(event), second.append_failure(event))

    asyncio.run(race())
    assert asyncio.run(first.read_failures(scene.run_id)) == (event,)
    assert asyncio.run(second.read_failures(scene.run_id)) == (event,)
