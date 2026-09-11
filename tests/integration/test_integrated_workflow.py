"""Real Controller/parser and canonical response/store integration; injected model only."""

from __future__ import annotations

import asyncio
import json
import runpy
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from appraisal_review.adapters.aws.action_selector import BedrockActionSelector, ModelSelectorConfig
from appraisal_review.adapters.local.approval import LocalApprovalStore, current_reviewer
from appraisal_review.adapters.local.decision_trace import NonDurableInMemoryDecisionTrace
from appraisal_review.adapters.local.service import LocalReviewService
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.adapters.local.sqlite_workflow_run_ledger import SqliteWorkflowRunLedger
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.integrated_workflow import (
    ControllerInvocation,
    IntegratedTaskBinding,
    IntegratedWorkflowExecution,
)
from appraisal_review.application.job_state import JobEvent
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.factor_models import AgentReviewRequest, AgentReviewRun
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    Budget,
    HumanResponse,
    Permission,
    ResponseAction,
    ReviewSubmission,
    ServiceErrorCode,
    TaskKind,
)
from appraisal_review.ports.jobs import ConditionFailed

ROOT = Path(__file__).resolve().parents[2]
CREATE = runpy.run_path(str(ROOT / "scripts/local_service_fixture.py"))["create_fixture"]
NOW = 1000


class StoredReviews:
    """Test binding of the production outcome port to a real immutable SQLite table."""

    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS workflow_reviews (run TEXT PRIMARY KEY, body TEXT)"
            )

    async def put(self, run, review):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT body FROM workflow_reviews WHERE run=?", (run.model_dump_json(),)
            ).fetchone()
            if previous is not None:
                assert AgentReviewRun.model_validate_json(previous[0]) == review
            else:
                db.execute(
                    "INSERT INTO workflow_reviews VALUES (?,?)",
                    (run.model_dump_json(), review.model_dump_json()),
                )

    async def read(self, run):
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "SELECT body FROM workflow_reviews WHERE run=?", (run.model_dump_json(),)
            ).fetchone()
        return None if row is None else AgentReviewRun.model_validate_json(row[0])


class SelectorClient:
    """Select from actual advertised input without contacting a model service."""

    def __init__(self):
        self.calls = []
        self.forge_origin = False

    def converse(self, **kwargs):
        value = json.loads(kwargs["messages"][0]["content"][0]["text"])
        self.calls.append(value)
        action = value["allowed_actions"]["actions"][0]
        if action["action"] == "deterministic_review":
            arguments = {
                "kind": action["action"],
                "revision": value["snapshot"]["revision"]["reference"],
                "rules": value["snapshot"]["revision"]["rules"],
            }
        else:
            blocker = value["snapshot"]["unresolved_blockers"][0]
            arguments = {
                "kind": action["action"],
                "question": "Review the exact side.",
                "reason_code": blocker["reason_code"],
                "affected_subject_ids": blocker["affected_subject_ids"],
                "evidence": blocker["evidence"],
            }
        result = {
            "action": action["action"],
            "action_id": action["action_id"],
            "arguments": arguments,
        }
        if self.forge_origin:
            result["proposer"] = {"actor_id": "forged-executor", "kind": "system"}
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": json.dumps(result)}]}},
        }


class Harness:
    @classmethod
    async def create(
        cls, root, *, confirmed=False, both_unconfirmed=False, stale_confirmation=False
    ):
        self = cls()
        self.root = root
        self.config = await CREATE(root / "inputs")
        self.local = LocalReviewService(self.config)
        self.request = AgentReviewRequest.model_validate_json(
            (root / "inputs/request.json").read_bytes()
        )
        reviewer = current_reviewer()
        self.principal = Principal(
            actor=ActorReference(actor_id=f"{reviewer.uid}:{reviewer.name}", kind="human"),
            case_ids=frozenset({"synthetic-case"}),
            permissions=frozenset(Permission),
        )
        self.allowed = True
        self.store = SQLiteReviewStore(root / "data/review.sqlite3", clock=lambda: NOW)
        self.service = HumanTaskService(self.store, clock=lambda: NOW, new_revision_id=lambda: "r2")
        material = self.local.snapshot.material
        material.policy.rule_sets[0].rules.status = "approved"
        if not confirmed:
            material.facts.pairs[0].target_reliability.confirmation = None
        if both_unconfirmed:
            material.facts.pairs[0].comparable_reliability.confirmation = None
        if stale_confirmation:
            material.facts.pairs[0].pair.target.raw_text = "changed synthetic observation"
        self.snapshot = RevisionSnapshot.capture(material, "r1")
        self.approval = LocalApprovalStore(self.config.approval_store)
        if confirmed and not stale_confirmation:
            self.approve(self.snapshot)
        self.job_id, self.run_id = uuid4(), uuid4()
        await self.store.create_job(
            self.principal,
            ReviewSubmission(
                revision=self.snapshot.revision.reference,
                documents=self.snapshot.revision.documents,
                idempotency_key="submission",
            ),
            job_id=self.job_id,
            run_id=self.run_id,
            now=NOW,
        )
        await self.store.register_tasks(
            job_id=self.job_id,
            principal_id=self.principal.actor.actor_id,
            snapshot=self.snapshot,
            tasks=(),
        )
        self.reviews = StoredReviews(root / "outputs.sqlite3")
        self.ledger = SqliteWorkflowRunLedger(root / "workflow.sqlite3")
        self.client = SelectorClient()
        self.trace = NonDurableInMemoryDecisionTrace()
        self.prepared = []
        self.registered = []
        return self

    def approve(self, snapshot):
        self.approval.approve(snapshot.material, expected_digest=content_digest(snapshot.material))

    async def claim(self):
        record = await self.store.read_job(job_id=self.job_id)
        attempt = await self.store.claim(
            job_id=self.job_id,
            run_id=record.current_run.run_id,
            owner=uuid4(),
            lease_seconds=60,
            now=NOW,
        )
        return await self.store.read_job(job_id=self.job_id), attempt

    async def read(self, principal_id, case_id):
        return self.principal

    async def require_current(self, principal, record, attempt, snapshot):
        if not self.allowed:
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        assert principal.actor.actor_id == record.principal_id
        await self.store.heartbeat(attempt, lease_seconds=60, now=NOW)
        # Controller's real parser checks byte hash and typed registry against these files.
        assert snapshot.revision.reference == record.current_run.revision

    async def register(self, record, attempt, snapshot, tasks):
        # The canonical store's actual transaction, including its attempt/fence check.
        # The composition owner can expose this as a public store adapter method.
        async def apply(jobs, task_store):
            self.store._authority(jobs, attempt, NOW)
            job = await jobs.read_job(job_id=record.job_id)
            if job.current_run != record.current_run or job.cancel_requested:
                raise ConditionFailed("Stale task registration")
            task_store.seed(
                job_id=record.job_id,
                principal_id=record.principal_id,
                snapshot=snapshot,
                tasks=tasks,
            )

        await self.store._execute(apply)
        self.registered.extend(tasks)

    async def controllers(self, principal, run, snapshot):
        self.prepared.append(snapshot)
        return ControllerInvocation(self.local.controller_factory(), self.request)

    def execution(self, *, bindings=None, reviews=None, project_result=None):
        snapshot = self.snapshot
        pair = snapshot.material.facts.pairs[0]
        findings = (
            CaseReviewer(None)
            .review(
                snapshot.material.policy, snapshot.material.facts, snapshot.material.policy.registry
            )
            .findings
        )
        binding = IntegratedTaskBinding(
            binding_id="target-road",
            kind=TaskKind.FACT,
            context=pair.context,
            factor_id=pair.pair.factor_id,
            side="target",
            finding_ids=tuple(
                f.id
                for f in findings
                if f.status != "verified" and f.kind not in {"approval", "calculation"}
            ),
            question="Confirm this exact measured road width.",
        )
        return IntegratedWorkflowExecution(
            human_tasks=self.service,
            principals=self,
            guard=self,
            registration=self,
            controllers=self.controllers,
            reviews=reviews or self.reviews,
            selector=BedrockActionSelector(
                self.client, ModelSelectorConfig(model_id="synthetic-only", attempts=1)
            ),
            ledger=self.ledger,
            trace=self.trace,
            bindings=(binding,) if bindings is None else bindings,
            budget=Budget(steps_remaining=4, model_calls_remaining=4, retries_remaining=0),
            project_result=project_result,
        )


def test_model_actions_pause_and_actual_response_resumes_exact_revision(tmp_path):
    async def scenario():
        h = await Harness.create(tmp_path)
        record, attempt = await h.claim()
        first = await h.execution().execute(record, attempt)
        assert first.result is None and len(first.persisted_task_ids) == 1
        assert len(h.client.calls) == 2
        assert [
            event.executed_action.value for event in await h.trace.read(record.current_run.run_id)
        ] == ["deterministic_review", "request_human_review"]
        task = (await h.service.list_tasks(h.principal, h.job_id)).tasks[0].task
        assert task.run == record.current_run
        assert task.side.input_digest
        assert task.reason_code == "controller-review-findings"
        assert task.affected_subject_ids == ("target-road",)
        before = await h.reviews.read(record.current_run)
        assert before.verification.can_complete is False
        assert before.case_review.coverage.missing
        assert before.case_review.findings
        assert h.prepared[0].material.facts.pairs[0].pair.target.confidence == 0
        # Replay of the same attempt reads actual stored tasks, without another selector.
        assert await h.execution().execute(record, attempt) == first
        assert len(h.client.calls) == 2 and len(h.prepared) == 1
        await h.store.finish(
            attempt, event=JobEvent.NEEDS_HUMAN, now=NOW, open_task_ids=first.persisted_task_ids
        )
        command = HumanResponse(
            task_id=task.task_id,
            expected_version=task.version,
            revision=task.run.revision,
            side_digest=task.side.input_digest,
            action=ResponseAction.CONFIRM,
            idempotency_key="confirm-target",
        )
        response = await h.service.respond(h.principal, task.task_id, command)
        assert await h.service.respond(h.principal, task.task_id, command) == response
        assert response.resumed_run.run_id != record.current_run.run_id
        next_snapshot = await h.store.read_snapshot(revision=response.revision)
        assert next_snapshot.revision.parent == h.snapshot.revision.reference
        assert next_snapshot.material.facts.pairs[0].pair.target.confidence == 0
        assert next_snapshot.material.facts.pairs[0].target_reliability.confirmation is not None
        assert not h.approval.permits(next_snapshot.material)
        # Separate explicit synthetic authorization, never a side effect of respond.
        h.approve(next_snapshot)
        next_record, next_attempt = await h.claim()
        result = await h.execution().execute(next_record, next_attempt)
        assert result.persisted_task_ids == ()
        assert result.result.business_status.value == "verified"
        assert result.result.run.revision == response.revision
        assert result.result.verification.status.value == "verified"
        assert all(f.status == "verified" for f in result.result.findings)
        assert h.prepared[-1] == next_snapshot
        assert len(h.prepared) == 2 and len(h.client.calls) == 3
        assert [r.reference.revision_id for r in await h.store.list_revisions(job_id=h.job_id)] == [
            "r1",
            "r2",
        ]

    asyncio.run(scenario())


def test_new_execution_instance_replays_verified_controller_receipt_without_reexecution(tmp_path):
    async def scenario():
        h = await Harness.create(tmp_path, confirmed=True)
        record, attempt = await h.claim()
        first = await h.execution().execute(record, attempt)
        h.reviews = StoredReviews(tmp_path / "outputs.sqlite3")
        h.ledger = SqliteWorkflowRunLedger(tmp_path / "workflow.sqlite3")
        assert await h.execution().execute(record, attempt) == first
        assert first.result.business_status.value == "verified"
        assert len(h.prepared) == len(h.client.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["revoked", "stale-attempt", "foreign-principal"])
def test_current_authority_failure_prevents_model_and_controller(tmp_path, failure):
    async def scenario():
        from dataclasses import replace

        h = await Harness.create(tmp_path, confirmed=True)
        record, attempt = await h.claim()
        if failure == "revoked":
            h.allowed = False
        elif failure == "stale-attempt":
            attempt = replace(attempt, fencing_token=attempt.fencing_token + 1)
        else:
            h.principal = Principal(
                actor=ActorReference(actor_id="foreign", kind="human"),
                case_ids=h.principal.case_ids,
                permissions=h.principal.permissions,
            )
        with pytest.raises((ServiceFault, ConditionFailed)):
            await h.execution().execute(record, attempt)
        assert not h.client.calls and not h.prepared and not h.registered

    asyncio.run(scenario())


def test_model_cannot_claim_executor_origin(tmp_path):
    async def scenario():
        h = await Harness.create(tmp_path, confirmed=True)
        h.client.forge_origin = True
        record, attempt = await h.claim()
        with pytest.raises(ServiceFault):
            await h.execution().execute(record, attempt)
        assert len(h.client.calls) == 1
        assert not h.prepared and not h.registered

    asyncio.run(scenario())


def test_unknown_controller_receipt_persistence_never_reexecutes(tmp_path):
    async def scenario():
        h = await Harness.create(tmp_path, confirmed=True)
        record, attempt = await h.claim()

        class UnknownStore:
            async def read(self, run):
                return None

            async def put(self, run, review):
                raise OSError("synthetic unknown persistence acknowledgment")

        execution = h.execution(reviews=UnknownStore())
        for _ in range(2):
            with pytest.raises(ServiceFault):
                await execution.execute(record, attempt)
        assert len(h.prepared) == len(h.client.calls) == 1
        events = await h.trace.read(record.current_run.run_id)
        assert events[0].reason_code == "tool-result-unknown"
        assert events[0].disposition == "failed"

    asyncio.run(scenario())


def test_cached_controller_receipt_digest_tamper_is_rejected(tmp_path):
    async def scenario():
        h = await Harness.create(tmp_path, confirmed=True)
        record, attempt = await h.claim()
        await h.execution().execute(record, attempt)
        review = await h.reviews.read(record.current_run)
        review.audit_events = []
        with closing(sqlite3.connect(tmp_path / "outputs.sqlite3")) as db, db:
            db.execute("UPDATE workflow_reviews SET body=?", (review.model_dump_json(),))
        with pytest.raises(ServiceFault):
            await h.execution().execute(record, attempt)
        assert len(h.prepared) == len(h.client.calls) == 1

    asyncio.run(scenario())


def test_controller_final_verification_blocks_real_writer_for_unconfirmed_material(tmp_path):
    async def scenario():
        h = await Harness.create(tmp_path)
        h.request = AgentReviewRequest.model_validate_json(
            (tmp_path / "inputs/request-write.json").read_bytes()
        )
        before = {spec.path: spec.path.read_bytes() for spec in h.config.inputs.documents}
        record, attempt = await h.claim()
        result = await h.execution().execute(record, attempt)
        assert result.persisted_task_ids
        assert not list(h.config.writer.output_directory.iterdir())
        report = await h.reviews.read(record.current_run)
        assert report.verification.can_complete is False
        assert report.artifact_status != "written"
        assert not any(event.tool == "write_pdf" for event in report.audit_events)
        assert {path: path.read_bytes() for path in before} == before

    asyncio.run(scenario())


def test_controller_real_pdf_and_exact_manifest_projection(tmp_path):
    async def scenario():
        import hashlib

        from pypdf import PdfReader

        from appraisal_review.adapters.local.service import public_verification
        from appraisal_review.domain.service_contracts import ExecutionStatus, ServiceResult

        h = await Harness.create(tmp_path, confirmed=True)
        h.request = AgentReviewRequest.model_validate_json(
            (tmp_path / "inputs/request-write.json").read_bytes()
        )
        writers = []

        async def controllers(principal, run, snapshot):
            h.prepared.append(snapshot)
            controller = h.local.controller_factory()
            controller.pdf_writer.run_id = run.run_id
            writers.append(controller.pdf_writer)
            return ControllerInvocation(controller, h.request)

        h.controllers = controllers

        async def project(record, attempt, report):
            manifests = h.local._manifest(
                report, h.request, record.current_run, writers[-1].evidence
            )
            return ServiceResult(
                run=record.current_run,
                result_version=attempt.expected_result_version + 1,
                execution_status=ExecutionStatus.SUCCEEDED,
                business_status=report.status,
                artifact_status=report.artifact_status,
                findings=tuple(report.case_review.findings),
                verification=public_verification(report.verification),
                artifacts=manifests,
            )

        before = {spec.path: spec.path.read_bytes() for spec in h.config.inputs.documents}
        record, attempt = await h.claim()
        result = await h.execution(project_result=project).execute(record, attempt)
        assert result.result.business_status.value == "completed"
        output = h.config.writer.output_directory / "completed.pdf"
        assert "+5.00%" in PdfReader(output).pages[0].extract_text()
        assert (
            result.result.artifacts[0].content_hash
            == hashlib.sha256(output.read_bytes()).hexdigest()
        )
        assert result.result.run == record.current_run
        assert {path: path.read_bytes() for path in before} == before

    asyncio.run(scenario())


def test_actual_response_does_not_reask_exact_confirmed_side(tmp_path):
    async def scenario():
        from dataclasses import replace

        h = await Harness.create(tmp_path, both_unconfirmed=True)
        base = h.execution().bindings[0]
        bindings = (base, replace(base, binding_id="comparable-road", side="comparable"))
        record, attempt = await h.claim()
        first = await h.execution(bindings=bindings).execute(record, attempt)
        assert len(first.persisted_task_ids) == 2
        task = next(
            view.task
            for view in (await h.service.list_tasks(h.principal, h.job_id)).tasks
            if view.task.side.side == "target"
        )
        await h.store.finish(
            attempt,
            event=JobEvent.NEEDS_HUMAN,
            now=NOW,
            open_task_ids=first.persisted_task_ids,
        )
        response = await h.service.respond(
            h.principal,
            task.task_id,
            HumanResponse(
                task_id=task.task_id,
                expected_version=task.version,
                revision=task.run.revision,
                side_digest=task.side.input_digest,
                action=ResponseAction.CONFIRM,
                idempotency_key="confirm-only-target",
            ),
        )
        record, attempt = await h.claim()
        second = await h.execution(bindings=bindings).execute(record, attempt)
        current = [
            view.task
            for view in (await h.service.list_tasks(h.principal, h.job_id)).tasks
            if view.task.task_id in second.persisted_task_ids
        ]
        assert [task.side.side for task in current] == ["comparable"]
        assert all(task.run.revision == response.revision for task in current)
        material = h.prepared[-1].material
        assert material.facts.pairs[0].pair.target.confidence == 0
        assert material.facts.pairs[0].pair.comparable.confidence == 0
        assert material.facts.pairs[0].target_reliability.confirmation is not None
        assert not h.approval.permits(material)
        assert (await h.reviews.read(record.current_run)).verification.can_complete is False

    asyncio.run(scenario())


def test_stale_confirmation_digest_still_requires_side_review(tmp_path):
    async def scenario():
        h = await Harness.create(tmp_path, confirmed=True, stale_confirmation=True)
        record, attempt = await h.claim()
        result = await h.execution().execute(record, attempt)
        tasks = (await h.service.list_tasks(h.principal, h.job_id)).tasks
        assert len(result.persisted_task_ids) == 1
        assert tasks[0].task.side.side == "target"
        pair = h.prepared[-1].material.facts.pairs[0]
        assert pair.target_reliability.confirmation is not None
        assert pair.target_reliability.confirmation.input_digest != tasks[0].task.side.input_digest
        assert pair.pair.target.confidence == 0
        assert (await h.reviews.read(record.current_run)).verification.can_complete is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "action,change",
    [
        (action, change)
        for action in (ResponseAction.CONFIRM, ResponseAction.REJECT)
        for change in (None, "unbacked", "wrong-run", "wrong-actor")
    ]
    + [(ResponseAction.CONFIRM, "cancelled")],
)
def test_legacy_response_conversion_requires_actual_canonical_commit(tmp_path, action, change):
    async def scenario():
        from appraisal_review.application.integrated_workflow import CanonicalResponseReceiptAdapter
        from appraisal_review.domain.service_contracts import AcceptedResponse, HumanResponseResult

        h = await Harness.create(tmp_path)
        record, attempt = await h.claim()
        waiting = await h.execution().execute(record, attempt)
        task = (await h.service.list_tasks(h.principal, h.job_id)).tasks[0].task
        await h.store.finish(
            attempt,
            event=JobEvent.NEEDS_HUMAN,
            now=NOW,
            open_task_ids=waiting.persisted_task_ids,
        )
        command = HumanResponse(
            task_id=task.task_id,
            expected_version=task.version,
            revision=task.run.revision,
            side_digest=task.side.input_digest,
            action=action,
            idempotency_key="canonical-response",
        )
        receipt = await h.service.respond(h.principal, task.task_id, command)
        answered = await h.store.read_task(task_id=task.task_id)
        snapshot = await h.store.read_snapshot(revision=receipt.revision or task.run.revision)
        event = HumanResponseResult(
            event_id=uuid4(),
            accepted=AcceptedResponse(command=command, actor=h.principal.actor),
            task=answered.task,
            revision=snapshot.revision,
            next_run=receipt.resumed_run,
        )
        if change == "unbacked":
            event = event.model_copy(
                update={
                    "accepted": event.accepted.model_copy(
                        update={
                            "command": command.model_copy(
                                update={"idempotency_key": "legacy-only-event"}
                            ),
                        }
                    )
                }
            )
        elif change == "wrong-run":
            if event.next_run is not None:
                event = event.model_copy(
                    update={"next_run": event.next_run.model_copy(update={"run_id": uuid4()})}
                )
            else:
                event = event.model_copy(
                    update={
                        "task": event.task.model_copy(
                            update={
                                "run": event.task.run.model_copy(update={"run_id": uuid4()}),
                            }
                        )
                    }
                )
        elif change == "wrong-actor":
            event = event.model_copy(
                update={
                    "accepted": event.accepted.model_copy(
                        update={
                            "actor": ActorReference(actor_id="other-reviewer", kind="human"),
                        }
                    )
                }
            )
        elif change == "cancelled":
            # A stored receipt remains historical evidence, but cannot be converted
            # into a claim that cancelled work is currently scheduled.
            # Use the real job API to change current authority.
            await h.store.cancel(job_id=h.job_id, now=NOW)
        adapter = CanonicalResponseReceiptAdapter(h.store)
        if change is None:
            assert await adapter.convert(h.principal, event) == receipt
            assert await adapter.convert(h.principal, event) == receipt
        else:
            with pytest.raises(ServiceFault):
                await adapter.convert(h.principal, event)
        assert await h.service.respond(h.principal, task.task_id, command) == receipt

    asyncio.run(scenario())


def test_production_sqlite_review_and_trace_adapters_replay_actual_controller(tmp_path):
    async def scenario():
        from appraisal_review.adapters.local.workflow_runtime import (
            SQLiteDecisionTrace,
            SQLiteWorkflowReviews,
        )

        h = await Harness.create(tmp_path, confirmed=True)
        h.reviews = SQLiteWorkflowReviews(h.store)
        h.trace = SQLiteDecisionTrace(h.store)
        record, attempt = await h.claim()
        first = await h.execution().execute(record, attempt)
        review = await h.reviews.read(record.current_run)
        events = await h.trace.read(record.current_run.run_id)
        assert review.verification.can_complete is True
        assert len(events) == 1
        reopened = SQLiteReviewStore(h.store.path, clock=lambda: NOW)
        h.reviews = SQLiteWorkflowReviews(reopened)
        h.trace = SQLiteDecisionTrace(reopened)
        h.ledger = SqliteWorkflowRunLedger(tmp_path / "workflow.sqlite3")
        assert await h.execution().execute(record, attempt) == first
        assert await h.reviews.read(record.current_run) == review
        assert await h.trace.read(record.current_run.run_id) == events
        assert len(h.prepared) == len(h.client.calls) == 1

    asyncio.run(scenario())


def test_four_actual_responses_cover_confirmed_context_without_reasking(tmp_path, monkeypatch):
    import importlib

    from appraisal_review.adapters.local.integrated_service import (
        CurrentSourceExecution,
        LocalDirectory,
        LocalMaterialCatalog,
    )
    from appraisal_review.adapters.local.workflow_runtime import (
        SQLiteDecisionTrace,
        SQLiteExecutionAuthority,
        SQLiteWorkflowReviews,
    )
    from appraisal_review.application.integrated_workflow import CanonicalResponseReceiptAdapter
    from appraisal_review.domain.service_contracts import AcceptedResponse, HumanResponseResult

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    helper = importlib.import_module("integration_fixture")

    async def scenario():
        fixture = await helper.create_integration_fixture(tmp_path / "case")
        h = Harness()
        h.root, h.config = tmp_path, fixture.configuration
        h.local = LocalReviewService(h.config)
        h.request = fixture.request.model_copy(
            update={
                "output_pdf_uri": None,
                "pdf_template_uri": None,
                "field_map": None,
            }
        )
        h.principal, h.snapshot = fixture.principal, fixture.snapshot
        h.store = SQLiteReviewStore(tmp_path / "review.sqlite", clock=lambda: NOW)
        h.service = HumanTaskService(
            h.store, clock=lambda: NOW, new_revision_id=lambda: str(uuid4())
        )
        h.reviews, h.trace = SQLiteWorkflowReviews(h.store), SQLiteDecisionTrace(h.store)
        h.ledger = SqliteWorkflowRunLedger(tmp_path / "ledger.sqlite")
        h.client, h.prepared, h.registered = SelectorClient(), [], []
        h.job_id, h.run_id = uuid4(), uuid4()
        directory = LocalDirectory({"synthetic-session-" + "x" * 32: h.principal})
        catalog = LocalMaterialCatalog(h.store)
        catalog.register(h.principal, h.snapshot)
        authority = SQLiteExecutionAuthority(h.store, fixture.documents, directory)
        h.require_current, h.register = authority.require_current, authority.register
        await h.store.create_job(
            h.principal,
            ReviewSubmission(
                revision=h.snapshot.revision.reference,
                documents=h.snapshot.revision.documents,
                idempotency_key="four-responses",
            ),
            job_id=h.job_id,
            run_id=h.run_id,
            now=NOW,
        )
        material = h.snapshot.material
        findings = (
            CaseReviewer(None)
            .review(material.policy, material.facts, material.policy.registry)
            .findings
        )
        bindings = tuple(
            IntegratedTaskBinding(
                binding_id=f"{pair.context.comparable_id}-{side}",
                kind=TaskKind.FACT,
                context=pair.context,
                factor_id=pair.pair.factor_id,
                side=side,
                finding_ids=tuple(
                    f.id
                    for f in findings
                    if f.context == pair.context
                    and f.kind in {"evidence_reliability", "observed_unresolved"}
                ),
                question="Confirm this exact synthetic observation.",
            )
            for pair in material.facts.pairs
            for side in ("target", "comparable")
        )
        allow_final_authority = False

        async def controllers(principal, run, snapshot):
            h.prepared.append(snapshot)
            controller = h.local.controller_factory()
            if allow_final_authority:
                controller.authorization = fixture.authorize(snapshot)
            return ControllerInvocation(controller, h.request)

        h.controllers = controllers
        answered_sides = set()
        for index in range(4):
            record, attempt = await h.claim()
            executor = CurrentSourceExecution(
                h.execution(bindings=bindings),
                directory,
                catalog,
                fixture.documents,
                h.store,
            )
            try:
                result = await executor.execute(record, attempt)
            except ServiceFault:
                pytest.fail(f"Execution failed after {index} real confirmations")
            tasks = [
                view.task
                for view in (await h.service.list_tasks(h.principal, h.job_id)).tasks
                if view.task.task_id in result.persisted_task_ids
            ]
            assert len(tasks) == 4 - index
            assert all(
                (task.side.context.key(), task.side.side) not in answered_sides for task in tasks
            )
            report = await h.reviews.read(record.current_run)
            assert report.verification.can_complete is False
            if index == 2:
                assert {task.side.context.key() for task in tasks} == {
                    material.facts.pairs[1].context.key()
                }
                first_context = [
                    f
                    for f in report.case_review.findings
                    if f.context == material.facts.pairs[0].context and f.status != "verified"
                ]
                assert {f.kind for f in first_context} >= {
                    "evidence_reliability",
                    "observed_unresolved",
                    "calculation",
                }
            if index == 2:
                from appraisal_review.application.integrated_workflow import _WorkflowSession
                from appraisal_review.domain.review_contracts import ReviewFinding

                session = _WorkflowSession(
                    h.execution(bindings=bindings), record, attempt, h.prepared[-1]
                )
                for kind in (
                    "evidence_reliability",
                    "observed_unresolved",
                    "calculation",
                    "source_identity",
                ):
                    unrelated = report.model_copy(deep=True)
                    unrelated.case_review.findings.append(
                        ReviewFinding(
                            id=f"unrelated-{kind}",
                            kind=kind,
                            status="needs_review",
                            context=material.facts.pairs[0].context,
                            factor_id=material.facts.pairs[0].pair.factor_id,
                            trace="An independent unresolved problem.",
                        )
                    )
                    with pytest.raises(ServiceFault):
                        session._tasks(unrelated)
                invalid_observation = report.model_copy(deep=True)
                observed = next(
                    f
                    for f in invalid_observation.case_review.findings
                    if f.id == "observed/c1-rate"
                )
                observed.trace = "Observed value/unit is unresolved"
                with pytest.raises(ServiceFault):
                    session._tasks(invalid_observation)
                failed_factor = report.model_copy(deep=True)
                factor = next(
                    f
                    for f in failed_factor.case_review.findings
                    if f.context == material.facts.pairs[0].context
                    and f.kind == "evidence_reliability"
                )
                factor.status = "failed"
                with pytest.raises(ServiceFault):
                    session._tasks(failed_factor)
            task = tasks[0]
            answered_sides.add((task.side.context.key(), task.side.side))
            await h.store.finish(
                attempt,
                event=JobEvent.NEEDS_HUMAN,
                now=NOW,
                open_task_ids=result.persisted_task_ids,
            )
            command = HumanResponse(
                task_id=task.task_id,
                expected_version=task.version,
                revision=task.run.revision,
                side_digest=task.side.input_digest,
                action=ResponseAction.CONFIRM,
                idempotency_key=f"confirm-{index}",
            )
            receipt = await h.service.respond(h.principal, task.task_id, command)
            stored_task = await h.store.read_task(task_id=task.task_id)
            snapshot = await h.store.read_snapshot(revision=receipt.revision)
            event = HumanResponseResult(
                event_id=uuid4(),
                accepted=AcceptedResponse(command=command, actor=h.principal.actor),
                task=stored_task.task,
                revision=snapshot.revision,
                next_run=receipt.resumed_run,
            )
            assert (
                await CanonicalResponseReceiptAdapter(h.store).convert(h.principal, event)
                == receipt
            )
            assert all(
                getattr(pair.pair, side).confidence == 0
                for pair in snapshot.material.facts.pairs
                for side in ("target", "comparable")
            )
        assert len(answered_sides) == 4
        allow_final_authority = True
        record, attempt = await h.claim()
        result = await CurrentSourceExecution(
            h.execution(bindings=bindings),
            directory,
            catalog,
            fixture.documents,
            h.store,
        ).execute(record, attempt)
        assert result.persisted_task_ids == ()
        assert result.result.business_status.value == "verified"
        assert result.result.verification.status.value == "verified"
        assert len(h.prepared) == 5
        assert len(await h.store.list_revisions(job_id=h.job_id)) == 5

    asyncio.run(scenario())
