"""Formal approval invariants: readiness policy, authority, binding, fences.

Each test targets a way the approval gate could be cheated: submitting past policy
blockers, deciding without the publish permission, replaying an old command onto a
withdrawn approval, publishing content that drifted from the approved hashes, or
downloading formal bytes after withdrawal.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from appraisal_review.adapters.local.approval_store import SQLiteApprovalStore
from appraisal_review.adapters.local.export_store import SQLiteExportStore
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.exports import ExportAssets, ExportService
from appraisal_review.application.report_approvals import ReportApprovalService
from appraisal_review.application.report_readiness import ReadinessPolicy, evaluate_readiness
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.calculation_snapshot import (
    CalculationSnapshot,
    SnapshotEntry,
    SnapshotSubject,
)
from appraisal_review.domain.official_export import ExportRequest
from appraisal_review.domain.official_table_mapping import TableMapping
from appraisal_review.domain.report_approval import (
    ApprovalDecisionCommand,
    SubmitReportApproval,
)
from appraisal_review.domain.service_contracts import Permission, RunReference
from appraisal_review.testing.job_store_contract import principal, submission

TABLES = ("table_3", "table_4", "table_5")
REQUIRED = tuple(f"{table}.case.example" for table in TABLES)


@dataclass
class FakeFilled:
    content: bytes
    sheet_name: str
    written_cells: tuple[str, ...]


class Snapshots:
    def __init__(self, snapshot: CalculationSnapshot) -> None:
        self.snapshot = snapshot

    def read(self, case_id: str, revision_id: str) -> CalculationSnapshot | None:
        reference = self.snapshot.revision
        if (case_id, revision_id) != (reference.case_id, reference.revision_id):
            return None
        return CalculationSnapshot.model_validate_json(self.snapshot.model_dump_json())


def mapping(table: str, digest: str) -> TableMapping:
    return TableMapping.model_validate(
        {
            "table": table,
            "template_name": f"{table}.xlsx",
            "template_digest": digest,
            "sheet_name": "visible",
            "bindings": [
                {
                    "cell": "C5",
                    "source": f"{table}.case.example",
                    "label": "example",
                    "value_kind": "decimal",
                    "precision": 2,
                }
            ],
        }
    )


def entry(value: str = "5") -> SnapshotEntry:
    return SnapshotEntry(
        state="present",
        value=Decimal(value),
        unit="percent_points",
        origin="computed",
        trace="matrix trace",
    )


def snapshot_for(run: Any, entries: dict[str, SnapshotEntry]) -> CalculationSnapshot:
    return CalculationSnapshot(
        revision=run.revision,
        district="Shulin",
        valuation_date=date(2022, 9, 1),
        rule_bundle_id="shulin-2022",
        rule_bundle_version="v1",
        subjects=(
            SnapshotSubject(subject_id="P001", role="comparison_base", label="base"),
            SnapshotSubject(subject_id="P002", role="comparable", label="c1"),
        ),
        entries=entries,
    )


@pytest.fixture
def scene(tmp_path: Path) -> dict[str, Any]:
    store = SQLiteReviewStore(tmp_path / "p" / "review.sqlite3")
    # The harness principal reviews only; deciding takes the existing publish permission.
    person = replace(
        principal(),
        permissions=frozenset({Permission.REVIEW, Permission.PUBLISH}),
    )
    job_id, run_id = uuid4(), uuid4()
    asyncio.run(store.create_job(person, submission(), job_id=job_id, run_id=run_id, now=1000))
    run = asyncio.run(store.read_job(job_id=job_id)).current_run
    templates, mappings = {}, {}
    for table in TABLES:
        path = tmp_path / f"{table}.xlsx"
        path.write_bytes(b"PK\x03\x04-template-" + table.encode())
        templates[table] = path
        mappings[table] = mapping(table, hashlib.sha256(path.read_bytes()).hexdigest())
    assets = ExportAssets.load(
        bundle_id="official-test", version="v1", templates=templates, mappings=mappings
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "policy_version": "formal-readiness-test-v1",
                "required": list(REQUIRED),
                "justified_absence_states": ["not_applicable", "confirmed_zero"],
            }
        )
    )
    policy = ReadinessPolicy.load(policy_path)
    snapshots = Snapshots(snapshot_for(run, {key: entry() for key in REQUIRED}))

    def filler(
        template: bytes, table_mapping: TableMapping, snap: CalculationSnapshot
    ) -> FakeFilled:
        body = json.dumps(
            {
                "table": table_mapping.table,
                "values": {
                    key: str(item.value)
                    for key, item in sorted(snap.entries.items())
                    if key.startswith(table_mapping.table)
                },
            },
            sort_keys=True,
        ).encode()
        return FakeFilled(
            content=b"PK\x03\x04" + body,
            sheet_name=table_mapping.sheet_name,
            written_cells=("C5",),
        )

    clock = {"now": 5000}
    jobs = ReviewJobService(store, store.results, policy=store.policy)
    approval_store = SQLiteApprovalStore(store)
    approvals = ReportApprovalService(
        jobs=jobs,
        store=approval_store,
        assets=assets,
        filler=filler,
        snapshots=snapshots,
        policy=policy,
        clock=lambda: clock["now"],
    )
    export_store = SQLiteExportStore(store)
    current_revision = {"value": run.revision}
    exports = ExportService(
        jobs=jobs,
        store=export_store,
        assets=assets,
        filler=filler,
        converter=None,
        snapshots=snapshots,
        approvals=approval_store,
        current_revision=lambda job: current_revision["value"],
    )
    return dict(
        store=store,
        person=person,
        job_id=job_id,
        run=run,
        assets=assets,
        snapshots=snapshots,
        approvals=approvals,
        approval_store=approval_store,
        exports=exports,
        export_store=export_store,
        clock=clock,
        current_revision=current_revision,
    )


def submit_cmd(h: dict[str, Any], key: str = "a1") -> SubmitReportApproval:
    run: RunReference = h["run"]
    return SubmitReportApproval(
        idempotency_key=key,
        run=RunReference(run_id=run.run_id, revision=run.revision),
        calculation_snapshot_digest=h["snapshots"].snapshot.digest(),
        template_bundle=h["assets"].bundle,
    )


def submit(h: dict[str, Any], key: str = "a1") -> Any:
    return asyncio.run(h["approvals"].submit(h["person"], h["job_id"], submit_cmd(h, key)))


def decide(h: dict[str, Any], approval_id: Any, decision: str, key: str, reason: str = "") -> Any:
    return asyncio.run(
        h["approvals"].decide(
            h["person"],
            h["job_id"],
            approval_id,
            ApprovalDecisionCommand(idempotency_key=key, decision=decision, reason=reason),
        )
    )


def export_formal(h: dict[str, Any], key: str = "f1", fmt: str = "xlsx") -> Any:
    run: RunReference = h["run"]
    request = ExportRequest(
        idempotency_key=key,
        run=RunReference(run_id=run.run_id, revision=run.revision),
        calculation_snapshot_digest=h["snapshots"].snapshot.digest(),
        template_bundle=h["assets"].bundle,
        requested_mode="formal",
        export_format=fmt,
    )
    return asyncio.run(h["exports"].submit(h["person"], h["job_id"], request))


class TestReadinessPolicy:
    def test_missing_required_value_blocks(self, scene: dict[str, Any]) -> None:
        entries = {key: entry() for key in REQUIRED[1:]}
        blocked = evaluate_readiness(snapshot_for(scene["run"], entries), scene["approvals"].policy)
        assert blocked.state == "pending_data"
        assert blocked.blockers[0].code == "required_value_missing"
        assert blocked.blockers[0].source_key == REQUIRED[0]

    def test_clearing_gaps_does_not_make_ready(self, scene: dict[str, Any]) -> None:
        # gaps == {} but the required values still are not there.
        sparse = snapshot_for(scene["run"], {REQUIRED[0]: entry()})
        assert not sparse.gaps
        result = evaluate_readiness(sparse, scene["approvals"].policy)
        assert result.state == "pending_data"
        assert len(result.blockers) == 2

    def test_justified_not_applicable_passes(self, scene: dict[str, Any]) -> None:
        entries = {key: entry() for key in REQUIRED[1:]}
        # v2 discipline: a lawful absence carries a human-confirmed ruling, not
        # just any non-blank trace string.
        entries[REQUIRED[0]] = SnapshotEntry(
            state="not_applicable",
            origin="human_confirmed",
            trace="ruled out by criteria page 7",
        )
        assert (
            evaluate_readiness(snapshot_for(scene["run"], entries), scene["approvals"].policy).state
            == "ready_to_submit"
        )

    def test_unjustified_not_applicable_blocks(self, scene: dict[str, Any]) -> None:
        entries = {key: entry() for key in REQUIRED[1:]}
        entries[REQUIRED[0]] = SnapshotEntry(state="not_applicable")
        result = evaluate_readiness(snapshot_for(scene["run"], entries), scene["approvals"].policy)
        assert result.blockers[0].code == "unjustified_not_applicable"

    def test_unjustified_confirmed_zero_blocks(self, scene: dict[str, Any]) -> None:
        entries = {key: entry() for key in REQUIRED[1:]}
        entries[REQUIRED[0]] = SnapshotEntry(state="confirmed_zero")
        result = evaluate_readiness(snapshot_for(scene["run"], entries), scene["approvals"].policy)
        assert result.blockers[0].code == "unjustified_confirmed_zero"


class TestSubmission:
    def test_blocked_snapshot_cannot_submit(self, scene: dict[str, Any]) -> None:
        scene["snapshots"].snapshot = snapshot_for(scene["run"], {REQUIRED[0]: entry()})
        with pytest.raises(ServiceFault):
            submit(scene)

    def test_submit_pins_workbook_hashes(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        assert approval.status == "submitted"
        assert set(approval.binding.workbook_hashes) == set(TABLES)
        assert approval.submitted_by == scene["person"].actor
        assert approval.submitted_at == 5000

    def test_exact_replay_returns_original(self, scene: dict[str, Any]) -> None:
        first, again = submit(scene), submit(scene)
        assert first.approval_id == again.approval_id

    def test_stale_snapshot_digest_conflicts(self, scene: dict[str, Any]) -> None:
        command = submit_cmd(scene).model_copy(update={"calculation_snapshot_digest": "9" * 64})
        with pytest.raises(ServiceFault):
            asyncio.run(scene["approvals"].submit(scene["person"], scene["job_id"], command))


class TestDecisions:
    def test_decision_requires_publish_permission(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        limited = replace(
            scene["person"],
            permissions=frozenset(scene["person"].permissions) - {Permission.PUBLISH},
        )
        with pytest.raises(ServiceFault):
            asyncio.run(
                scene["approvals"].decide(
                    limited,
                    scene["job_id"],
                    approval.approval_id,
                    ApprovalDecisionCommand(idempotency_key="d1", decision="approve"),
                )
            )

    def test_stranger_cannot_read_or_decide(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        stranger = replace(scene["person"], case_ids=frozenset())
        with pytest.raises(ServiceFault):
            asyncio.run(scene["approvals"].read(stranger, scene["job_id"], approval.approval_id))

    def test_approve_records_actor_and_server_time(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        scene["clock"]["now"] = 6000
        decided = decide(scene, approval.approval_id, "approve", "d1")
        assert decided.status == "approved"
        assert decided.decision.actor == scene["person"].actor
        assert decided.decision.decided_at == 6000

    def test_return_requires_reason(self, scene: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="records why"):
            ApprovalDecisionCommand(idempotency_key="d1", decision="return")

    def test_replayed_decision_returns_original(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        first = decide(scene, approval.approval_id, "approve", "d1")
        again = decide(scene, approval.approval_id, "approve", "d1")
        assert first == again

    def test_replayed_approve_cannot_resurrect_withdrawn(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        decide(scene, approval.approval_id, "approve", "d1")
        decide(scene, approval.approval_id, "withdraw", "d2", reason="numbers under review")
        # a NEW approve command (different key) against a withdrawn approval conflicts
        with pytest.raises(ServiceFault):
            decide(scene, approval.approval_id, "approve", "d3")
        # the replayed original returns its own historical outcome, not a state change
        replay = decide(scene, approval.approval_id, "approve", "d1")
        assert replay.status == "approved"
        assert (
            scene["approval_store"].current_status(scene["job_id"], approval.approval_id)
            == "withdrawn"
        )


class TestFormalExports:
    def test_formal_without_approval_conflicts(self, scene: dict[str, Any]) -> None:
        with pytest.raises(ServiceFault):
            export_formal(scene)

    def test_formal_after_approval_publishes_and_binds(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        decide(scene, approval.approval_id, "approve", "d1")
        operation = export_formal(scene)
        assert operation.effective_mode == "formal"
        assert operation.approval_id == approval.approval_id
        asyncio.run(scene["exports"].run_pending())
        done = scene["export_store"].read(scene["job_id"], operation.export_id)
        assert done.status == "succeeded"
        for artifact in done.artifacts:
            assert artifact.content_hash == approval.binding.workbook_hashes[artifact.table]

    def test_withdrawal_blocks_new_formal_and_downloads(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        decide(scene, approval.approval_id, "approve", "d1")
        export_formal(scene)
        asyncio.run(scene["exports"].run_pending())
        decide(scene, approval.approval_id, "withdraw", "d2", reason="figure disputed")
        with pytest.raises(ServiceFault):
            export_formal(scene, key="f2")
        # already-delivered rows exist, but the live-status check the content route uses
        # refuses them now
        assert (
            scene["approval_store"].current_status(scene["job_id"], approval.approval_id)
            != "approved"
        )

    def test_withdrawal_during_execution_publishes_nothing(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        decide(scene, approval.approval_id, "approve", "d1")
        operation = export_formal(scene)
        decide(scene, approval.approval_id, "withdraw", "d2", reason="late dispute")
        asyncio.run(scene["exports"].run_pending())
        done = scene["export_store"].read(scene["job_id"], operation.export_id)
        assert done.status == "failed"
        assert done.artifacts == ()

    def test_content_drift_after_approval_conflicts(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        decide(scene, approval.approval_id, "approve", "d1")
        drifted = {key: entry() for key in REQUIRED}
        drifted[REQUIRED[0]] = entry("7")
        scene["snapshots"].snapshot = snapshot_for(scene["run"], drifted)
        with pytest.raises(ServiceFault):
            export_formal(scene, key="f2")

    def test_draft_path_needs_no_approval(self, scene: dict[str, Any]) -> None:
        run: RunReference = scene["run"]
        request = ExportRequest(
            idempotency_key="draft-1",
            run=RunReference(run_id=run.run_id, revision=run.revision),
            calculation_snapshot_digest=scene["snapshots"].snapshot.digest(),
            template_bundle=scene["assets"].bundle,
            requested_mode="draft",
            export_format="xlsx",
        )
        operation = asyncio.run(scene["exports"].submit(scene["person"], scene["job_id"], request))
        asyncio.run(scene["exports"].run_pending())
        assert (
            scene["export_store"].read(scene["job_id"], operation.export_id).status == "succeeded"
        )


class TestStalePublicationFence:
    """P1-B: a queued formal export must not publish after the case moved on."""

    def test_prefix_reproduction_queued_v1_publishes_after_revision_change(
        self, scene: dict[str, Any]
    ) -> None:
        approval = submit(scene)
        decide(scene, approval.approval_id, "approve", "d1")
        operation = export_formal(scene)
        # The case advances to a new revision before the worker runs. The old
        # behaviour consulted nothing current and published V1 as formal.
        scene["current_revision"]["value"] = scene["run"].revision.model_copy(
            update={"revision_id": str(uuid4())}
        )
        asyncio.run(scene["exports"].run_pending())
        done = scene["export_store"].read(scene["job_id"], operation.export_id)
        assert done.status == "failed", "stale queued formal export must not publish"
        assert done.artifacts == ()

    def test_valid_unchanged_revision_still_publishes(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        decide(scene, approval.approval_id, "approve", "d1")
        operation = export_formal(scene)
        asyncio.run(scene["exports"].run_pending())
        assert (
            scene["export_store"].read(scene["job_id"], operation.export_id).status == "succeeded"
        )

    def test_content_route_refuses_stale_formal_bytes(self, scene: dict[str, Any]) -> None:
        approval = submit(scene)
        decide(scene, approval.approval_id, "approve", "d1")
        operation = export_formal(scene)
        asyncio.run(scene["exports"].run_pending())
        # Delivered while V1 was current; then the case advances.
        done = scene["export_store"].read(scene["job_id"], operation.export_id)
        artifact = done.artifacts[0]
        found = scene["export_store"].find_delivered(scene["job_id"], artifact.artifact_id)
        assert found is not None
        stale_current = scene["run"].revision.model_copy(update={"revision_id": str(uuid4())})
        from appraisal_review.adapters.local.integrated_service import formal_delivery_allowed

        assert (
            formal_delivery_allowed(
                operation=done,
                approval_status="approved",
                current_revision_id=stale_current.revision_id,
            )
            is False
        )
        assert (
            formal_delivery_allowed(
                operation=done,
                approval_status="approved",
                current_revision_id=scene["run"].revision.revision_id,
            )
            is True
        )
        assert (
            formal_delivery_allowed(
                operation=done,
                approval_status="withdrawn",
                current_revision_id=scene["run"].revision.revision_id,
            )
            is False
        )


class TestHumanOnlyApproval:
    """P2-A: publish permission alone is not enough - the decider must be human."""

    @pytest.mark.parametrize("kind", ["system", "model"])
    def test_non_human_actor_with_publish_cannot_decide(
        self, scene: dict[str, Any], kind: str
    ) -> None:
        from appraisal_review.domain.service_contracts import ActorReference

        approval = submit(scene)
        machine = replace(
            scene["person"],
            actor=ActorReference(actor_id=f"{kind}-decider", kind=kind),
        )
        with pytest.raises(ServiceFault):
            asyncio.run(
                scene["approvals"].decide(
                    machine,
                    scene["job_id"],
                    approval.approval_id,
                    ApprovalDecisionCommand(idempotency_key="dx", decision="approve"),
                )
            )
        assert (
            scene["approval_store"].current_status(scene["job_id"], approval.approval_id)
            == "submitted"
        )


class TestDuplicateSubmissionConsistency:
    """P2-B: resubmitting the same content must not shadow a live approval."""

    def test_second_key_same_binding_reuses_the_live_request(self, scene: dict[str, Any]) -> None:
        first = submit(scene, key="a1")
        second = submit(scene, key="a2")
        assert second.approval_id == first.approval_id

    def test_resubmission_does_not_shadow_an_approved_binding(self, scene: dict[str, Any]) -> None:
        approval = submit(scene, key="a1")
        decide(scene, approval.approval_id, "approve", "d1")
        again = submit(scene, key="a3")
        assert again.approval_id == approval.approval_id
        assert again.status == "approved"
        # And formal export still publishes under the original approval.
        operation = export_formal(scene, key="f-shadow")
        asyncio.run(scene["exports"].run_pending())
        assert (
            scene["export_store"].read(scene["job_id"], operation.export_id).status == "succeeded"
        )

    def test_after_withdrawal_a_fresh_submission_is_new(self, scene: dict[str, Any]) -> None:
        approval = submit(scene, key="a1")
        decide(scene, approval.approval_id, "approve", "d1")
        decide(scene, approval.approval_id, "withdraw", "d2", reason="teardown")
        fresh = submit(scene, key="a4")
        assert fresh.approval_id != approval.approval_id
        assert fresh.status == "submitted"
        assert (
            scene["approval_store"].current_status(scene["job_id"], approval.approval_id)
            == "withdrawn"
        )
