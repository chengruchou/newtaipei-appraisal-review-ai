"""Scoped non-applicability never grants source, formula or content approval."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.review_database import ReviewTransaction, SQLiteReviewDatabase
from appraisal_review.adapters.local.sqlite_human_task_store import SQLiteHumanTaskStore
from appraisal_review.adapters.local.sqlite_job_store import SQLiteJobStore, SQLiteResultStore
from appraisal_review.application.human_tasks import HumanTaskService
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.application.source_processing import SourceProcessingScope
from appraisal_review.domain.case_review import CaseReviewer
from appraisal_review.domain.factor_models import WorkflowStatus
from appraisal_review.domain.service_contracts import (
    ExecutionStatus,
    Permission,
    ReviewSubmission,
    ServiceErrorCode,
    ServiceResult,
)
from appraisal_review.testing.job_store_contract import NOW
from tests.integration.test_sqlite_human_tasks import rows
from tests.unit.test_case_review import ApprovedFixture, arithmetic_material
from tests.unit.test_human_task_service import Harness, caller, correcting, correction_task


def configured(snapshot: RevisionSnapshot, version: str = "1") -> SourceProcessingScope:
    return SourceProcessingScope("synthetic-formula-batch", version, snapshot.revision.documents)


def command(snapshot: RevisionSnapshot, key: str = "submit") -> ReviewSubmission:
    return ReviewSubmission(
        revision=snapshot.revision.reference,
        documents=snapshot.revision.documents,
        idempotency_key=key,
    )


@pytest.mark.parametrize("change", ["case", "version", "hash", "purpose", "extra", "missing"])
def test_scope_does_not_extend_to_unknown_sources(tmp_path: Path, change: str) -> None:
    async def scenario() -> None:
        snapshot = Harness().snapshot
        scope = configured(snapshot)
        store = SQLiteJobStore(
            SQLiteReviewDatabase(tmp_path / "review.sqlite"), source_scopes=(scope,)
        )
        submitted = command(snapshot)
        documents = list(submitted.documents)
        if change == "case":
            documents = [
                document.model_copy(update={"case_id": "future-case"}) for document in documents
            ]
            submitted = submitted.model_copy(
                update={
                    "revision": submitted.revision.model_copy(update={"case_id": "future-case"})
                }
            )
        elif change == "version":
            documents[0] = documents[0].model_copy(update={"version": "new-version"})
        elif change == "hash":
            documents[0] = documents[0].model_copy(update={"content_hash": "f" * 64})
        elif change == "purpose":
            documents[0] = documents[0].model_copy(update={"purpose": "reference"})
        elif change == "extra":
            documents.append(documents[0].model_copy(update={"document_id": "extra-source"}))
        else:
            # A partial source set must not match even if a submission validator
            # refuses it before admission because mandatory sources are absent.
            assert not scope.matches(tuple(documents[:1]))
            return
        submitted = submitted.model_copy(update={"documents": tuple(documents)})
        principal = caller(case_id=submitted.revision.case_id)
        record, _ = await store.create_job(
            principal, submitted, job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        assert await store.read_source_processing(run_id=record.current_run.run_id) is None

    asyncio.run(scenario())


def test_scope_is_versioned_pinned_and_not_a_restart_default(tmp_path: Path) -> None:
    async def scenario() -> None:
        snapshot = Harness().snapshot
        path = tmp_path / "review.sqlite"
        scope = configured(snapshot)
        store = SQLiteJobStore(SQLiteReviewDatabase(path), source_scopes=(scope,))
        job, _ = await store.create_job(
            caller(), command(snapshot), job_id=uuid4(), run_id=uuid4(), now=NOW
        )
        assert job.status == "queued" and not job.open_task_ids
        assert await store.read_source_processing(run_id=job.current_run.run_id) == scope
        newer = SQLiteJobStore(
            SQLiteReviewDatabase(path), source_scopes=(configured(snapshot, "2"),)
        )
        replay, created = await newer.create_job(
            caller(), command(snapshot), job_id=uuid4(), run_id=uuid4(), now=NOW + 1
        )
        assert not created and replay == job
        assert await newer.read_source_processing(run_id=job.current_run.run_id) == scope
        without_configuration = SQLiteJobStore(SQLiteReviewDatabase(path))
        another, _ = await without_configuration.create_job(
            caller(), command(snapshot, "another"), job_id=uuid4(), run_id=uuid4(), now=NOW + 2
        )
        assert (
            await without_configuration.read_source_processing(run_id=another.current_run.run_id)
            is None
        )
        assert (
            await without_configuration.read_source_processing(run_id=job.current_run.run_id)
            == scope
        )

    asyncio.run(scenario())


def test_scope_version_cannot_be_reinterpreted(tmp_path: Path) -> None:
    snapshot = Harness().snapshot
    path = tmp_path / "review.sqlite"
    scope = configured(snapshot)
    store = SQLiteJobStore(SQLiteReviewDatabase(path), source_scopes=(scope,))
    before = rows(store.database)
    different = replace(
        scope,
        documents=(
            scope.documents[0].model_copy(update={"version": "different"}),
            *scope.documents[1:],
        ),
    )
    with pytest.raises(ServiceFault) as error:
        SQLiteJobStore(SQLiteReviewDatabase(path), source_scopes=(different,))
    assert error.value.problem.code == ServiceErrorCode.CONFLICT
    assert rows(store.database) == before


@pytest.mark.parametrize("invalid", ["passed", "empty", "duplicate", "mixed_case", "ambiguous"])
def test_invalid_or_ambiguous_source_settings_are_refused(tmp_path, invalid) -> None:
    scope = configured(Harness().snapshot)
    with pytest.raises(ValueError):
        if invalid == "passed":
            replace(scope, privacy_handling="passed")
        elif invalid == "empty":
            replace(scope, documents=())
        elif invalid == "duplicate":
            replace(scope, documents=(scope.documents[0], scope.documents[0]))
        elif invalid == "mixed_case":
            replace(
                scope,
                documents=(
                    scope.documents[0],
                    scope.documents[1].model_copy(update={"case_id": "other-case"}),
                ),
            )
        else:
            SQLiteJobStore(
                SQLiteReviewDatabase(tmp_path / "review.sqlite"),
                source_scopes=(scope, replace(scope, version="2")),
            )


def test_request_cannot_assert_processing_scope() -> None:
    payload = command(Harness().snapshot).model_dump(mode="json")
    payload["source_processing"] = {"privacy_handling": "not_applicable"}
    with pytest.raises(ValidationError):
        ReviewSubmission.model_validate(payload)


def test_correction_preserves_scope_but_still_needs_current_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = Harness()
        path = tmp_path / "review.sqlite"
        scope = configured(harness.snapshot)
        jobs = SQLiteJobStore(SQLiteReviewDatabase(path), source_scopes=(scope,))
        tasks = SQLiteHumanTaskStore(jobs)
        service = HumanTaskService(tasks, clock=lambda: NOW + 2)
        task = correction_task(harness.snapshot, harness.run_id)
        await jobs.create_job(
            caller(),
            command(harness.snapshot),
            job_id=harness.job_id,
            run_id=harness.run_id,
            now=NOW,
        )
        attempt = await jobs.claim(
            job_id=harness.job_id, run_id=harness.run_id, owner=uuid4(), lease_seconds=60, now=NOW
        )
        await tasks.finish_with_tasks(
            attempt, snapshot=harness.snapshot, tasks=(task,), now=NOW + 1
        )
        correction = correcting(harness, task)
        receipt = await service.respond(caller(), task.task_id, correction)
        reopened = SQLiteJobStore(SQLiteReviewDatabase(path))
        assert await reopened.read_source_processing(run_id=receipt.resumed_run.run_id) == scope
        next_tasks = SQLiteHumanTaskStore(reopened)
        next_service = HumanTaskService(next_tasks)
        assert await next_service.respond(caller(), task.task_id, correction) == receipt
        assert len(await next_tasks.list_revisions(job_id=harness.job_id)) == 2
        with pytest.raises(ServiceFault) as error:
            await next_service.respond(
                replace(caller(), permissions=frozenset({Permission.REVIEW})),
                task.task_id,
                correction,
            )
        assert error.value.problem.code == ServiceErrorCode.UNAUTHORIZED
        changed = await next_tasks.read_snapshot(revision=receipt.revision)
        evaluation = CaseReviewer(ApprovedFixture(harness.snapshot.material)).review(
            changed.material.policy, changed.material.facts, changed.material.policy.registry
        )
        assert evaluation.status != "verified"
        with pytest.raises(ServiceFault) as error:
            await next_service.respond(caller(), task.task_id, correcting(harness, task, metres=13))
        assert error.value.problem.code == ServiceErrorCode.CONFLICT

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "condition",
    [
        "valid",
        "no_approval",
        "missing_value",
        "low_confidence",
        "wrong_formula",
        "unsupported_rule",
        "source_version",
    ],
)
def test_privacy_not_applicable_does_not_bypass_review(tmp_path, condition) -> None:
    async def scenario() -> None:
        material = arithmetic_material()
        if condition == "missing_value":
            material.facts.pairs[0].pair.target.value = None
        elif condition == "low_confidence":
            material.facts.pairs[0].pair.target.confidence = 0
        elif condition == "wrong_formula":
            next(
                value for value in material.facts.observed if value.slot_id == "copied"
            ).value = "999"
        elif condition == "unsupported_rule":
            material.policy.inventory.unsupported.append("unsupported synthetic formula")
        elif condition == "source_version":
            material.policy.rule_sets[0].source_version = "other-version"
        snapshot = RevisionSnapshot.capture(material, "review-r1")
        db = SQLiteReviewDatabase(tmp_path / "review.sqlite")
        jobs = SQLiteJobStore(db, source_scopes=(configured(snapshot),))
        service = ReviewJobService(jobs, SQLiteResultStore(db), clock=lambda: NOW)
        accepted = await service.submit(caller(), command(snapshot))
        job_id, run_id = accepted.acceptance.job.job_id, accepted.acceptance.run.run_id
        assert (
            await jobs.read_source_processing(run_id=run_id)
        ).privacy_handling == "not_applicable"
        attempt = await service.claim(job_id=job_id, run_id=run_id, owner=uuid4())
        authorization = None if condition == "no_approval" else ApprovedFixture(material)
        evaluated = CaseReviewer(authorization).review(
            material.policy, material.facts, material.policy.registry
        )
        if condition == "valid":
            assert evaluated.status == "verified"
        else:
            assert evaluated.status in {"failed", "needs_review"}
        result = ServiceResult(
            run=accepted.acceptance.run,
            result_version=1,
            execution_status=ExecutionStatus.SUCCEEDED,
            business_status=WorkflowStatus.VERIFIED
            if evaluated.status == "verified"
            else WorkflowStatus.NEEDS_REVIEW,
            findings=tuple(evaluated.findings),
        )
        await service.publish(attempt, result)
        stored = await service.result(caller(), job_id)
        assert stored.business_status != WorkflowStatus.COMPLETED
        assert stored.artifact_status == "not_requested"
        if condition != "valid":
            assert stored.business_status == WorkflowStatus.NEEDS_REVIEW

    asyncio.run(scenario())


def test_scope_and_submission_commit_atomically(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        snapshot = Harness().snapshot
        jobs = SQLiteJobStore(
            SQLiteReviewDatabase(tmp_path / "review.sqlite"), source_scopes=(configured(snapshot),)
        )
        before = rows(jobs.database)
        original = ReviewTransaction.put

        def fail(self, kind, key, value, subkey="", *, insert=False):
            original(self, kind, key, value, subkey, insert=insert)
            if kind == "run_source_processing":
                raise RuntimeError("injected scope write failure")

        with monkeypatch.context() as patch:
            patch.setattr(ReviewTransaction, "put", fail)
            with pytest.raises(RuntimeError):
                await jobs.create_job(
                    caller(), command(snapshot), job_id=uuid4(), run_id=uuid4(), now=NOW
                )
        assert rows(jobs.database) == before

    asyncio.run(scenario())


def test_scope_and_correction_continuation_roll_back_together(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        harness = Harness()
        jobs = SQLiteJobStore(
            SQLiteReviewDatabase(tmp_path / "review.sqlite"),
            source_scopes=(configured(harness.snapshot),),
        )
        tasks = SQLiteHumanTaskStore(jobs)
        service = HumanTaskService(tasks, clock=lambda: NOW + 2)
        task = correction_task(harness.snapshot, harness.run_id)
        await jobs.create_job(
            caller(),
            command(harness.snapshot),
            job_id=harness.job_id,
            run_id=harness.run_id,
            now=NOW,
        )
        attempt = await jobs.claim(
            job_id=harness.job_id, run_id=harness.run_id, owner=uuid4(), lease_seconds=60, now=NOW
        )
        await tasks.finish_with_tasks(
            attempt, snapshot=harness.snapshot, tasks=(task,), now=NOW + 1
        )
        before = rows(jobs.database)
        original = ReviewTransaction.put

        def fail(self, kind, key, value, subkey="", *, insert=False):
            original(self, kind, key, value, subkey, insert=insert)
            if kind == "run_source_processing":
                raise RuntimeError("injected continuation scope failure")

        with monkeypatch.context() as patch:
            patch.setattr(ReviewTransaction, "put", fail)
            with pytest.raises(RuntimeError):
                await service.respond(caller(), task.task_id, correcting(harness, task))
        assert rows(jobs.database) == before
        receipt = await service.respond(caller(), task.task_id, correcting(harness, task))
        assert await jobs.read_source_processing(run_id=receipt.resumed_run.run_id) == configured(
            harness.snapshot
        )
        assert len(await tasks.list_revisions(job_id=harness.job_id)) == 2

    asyncio.run(scenario())
