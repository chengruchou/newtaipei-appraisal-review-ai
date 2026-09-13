"""Fact adoption invariants: CAS revision advance, atomic batches, exact replay.

Each test targets a way adoption could lie: showing success while one candidate
failed, adopting over a revision that moved, double-adopting on a retry,
registering numbers the recompute hook rewrote, or leaving readiness/exports
reading the pre-adoption snapshot.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from appraisal_review.adapters.local.adoption_store import SQLiteAdoptionStore
from appraisal_review.adapters.local.candidate_store import SQLiteCandidateStore
from appraisal_review.adapters.local.snapshot_registry import RegisteredSnapshots
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.api.app import FAULT_STATUS
from appraisal_review.api.routes.fact_adoption import router as fact_adoption_router
from appraisal_review.application.fact_adoption import (
    AdoptFactsCommand,
    AdoptionRefusal,
    AdoptionResult,
    FactAdoptionService,
)
from appraisal_review.application.fact_candidates import (
    CandidateConfirmation,
    CandidateService,
    ConfirmCandidateCommand,
    FactCandidate,
    RegisterCandidateCommand,
)
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.calculation_snapshot import (
    CalculationSnapshot,
    SnapshotEntry,
    SnapshotSubject,
)
from appraisal_review.domain.service_contracts import (
    ActorReference,
    Permission,
    ServiceErrorCode,
)
from appraisal_review.testing.job_store_contract import principal, submission

NOW = 1_757_600_000
CASE_ID = "case-contract"
DISTANCE_KEY = "table_5.P002.market_distance"
PARK_KEY = "table_5.P002.park_distance"
KEPT_GAP = "table_4.case.other"
EVIDENCE_SHA = hashlib.sha256(b'[{"name": "example park"}]').hexdigest()


def person(**overrides: Any) -> Principal:
    base = replace(
        principal(),
        permissions=frozenset({Permission.REVIEW, Permission.CONFIRM, Permission.CORRECT}),
    )
    return replace(base, **overrides)


def register_command(key: str, field_key: str, value: str) -> RegisterCandidateCommand:
    return RegisterCandidateCommand(
        idempotency_key=key,
        revision_id="rev-1",
        subject_id="P002",
        field_key=field_key,
        value=value,
        unit="m",
        applicable_date="2026-09-12",
        source_id="ntpc-parks",
        evidence={
            "url": "https://data.ntpc.gov.tw/api/datasets/x/json?page=0&size=1000",
            "sha256": EVIDENCE_SHA,
            "retrieved_at": NOW - 60,
            "excerpt": '{"name": "example park"}',
            "row_locator": "page=0,row=17",
        },
    )


def confirm_command(
    candidate: FactCandidate, *, key: str, decision: str = "accept"
) -> ConfirmCandidateCommand:
    return ConfirmCandidateCommand.model_validate(
        {
            "idempotency_key": key,
            "decision": decision,
            "reason": "not the published figure" if decision == "reject" else None,
            "expected_revision": candidate.revision_id,
            "accepted_value": candidate.value,
            "accepted_unit": candidate.unit,
            "accepted_applicable_date": candidate.applicable_date,
            "evidence_sha256": candidate.evidence.sha256,
        }
    )


def base_snapshot(run: Any) -> CalculationSnapshot:
    return CalculationSnapshot(
        revision=run.revision,
        district="Shulin",
        valuation_date="2022-09-01",
        rule_bundle_id="shulin-2022",
        rule_bundle_version="v1",
        subjects=(
            SnapshotSubject(subject_id="P001", role="comparison_base", label="base"),
            SnapshotSubject(subject_id="P002", role="comparable", label="c1"),
        ),
        entries={
            "table_3.case.example": SnapshotEntry(
                state="present",
                value="5",
                unit="percent_points",
                origin="computed",
                trace="matrix trace",
            )
        },
        gaps={
            DISTANCE_KEY: "awaiting_external_confirmation",
            PARK_KEY: "awaiting_external_confirmation",
            KEPT_GAP: "still_awaiting_something_else",
        },
    )


def make_scene(tmp_path: Path, *, register_base: bool = True) -> dict[str, Any]:
    store = SQLiteReviewStore(tmp_path / "p" / "review.sqlite3")
    human = person()
    job_id, run_id = uuid4(), uuid4()
    asyncio.run(store.create_job(human, submission(), job_id=job_id, run_id=run_id, now=1000))
    run = asyncio.run(store.read_job(job_id=job_id)).current_run
    registry = RegisteredSnapshots(tmp_path / "snapshots")
    if register_base:
        registry.register(base_snapshot(run))
    candidate_store = SQLiteCandidateStore(store)
    candidate_service = CandidateService(store=candidate_store, clock=lambda: NOW)
    adoption_store = SQLiteAdoptionStore(store, snapshots=registry)
    hook_inputs: list[CalculationSnapshot] = []
    scene: dict[str, Any] = dict(
        store=store,
        person=human,
        job_id=job_id,
        run=run,
        registry=registry,
        candidate_store=candidate_store,
        candidate_service=candidate_service,
        adoption_store=adoption_store,
        hook_inputs=hook_inputs,
    )

    def recompute(snapshot: CalculationSnapshot) -> CalculationSnapshot:
        hook_inputs.append(snapshot)
        hook = scene.get("recompute_override")
        return snapshot if hook is None else hook(snapshot)

    scene["service"] = FactAdoptionService(
        candidates=candidate_store,
        snapshots=registry,
        jobs=ReviewJobService(store, store.results, policy=store.policy),
        store=adoption_store,
        recompute=recompute,
        clock=lambda: NOW,
    )
    return scene


@pytest.fixture
def scene(tmp_path: Path) -> dict[str, Any]:
    return make_scene(tmp_path)


def confirmed(
    scene: dict[str, Any], *, key: str, field_key: str, value: str, decision: str = "accept"
) -> tuple[FactCandidate, CandidateConfirmation]:
    service: CandidateService = scene["candidate_service"]
    candidate = asyncio.run(
        service.register_candidate(
            scene["person"], CASE_ID, register_command(key, field_key, value)
        )
    )
    receipt = asyncio.run(
        service.confirm(
            scene["person"],
            CASE_ID,
            candidate.candidate_id,
            confirm_command(candidate, key=f"decide-{key}", decision=decision),
        )
    )
    return scene["candidate_store"].read(CASE_ID, candidate.candidate_id), receipt


def adopt(
    scene: dict[str, Any],
    candidate_ids: tuple[str, ...],
    *,
    key: str = "adopt-1",
    expected: str | None = None,
    actor: Principal | None = None,
) -> AdoptionResult:
    command = AdoptFactsCommand(
        idempotency_key=key,
        expected_revision=expected or scene["run"].revision.revision_id,
        candidate_ids=candidate_ids,
    )
    return asyncio.run(scene["service"].adopt(actor or scene["person"], scene["job_id"], command))


def current_run(scene: dict[str, Any]) -> Any:
    return asyncio.run(scene["store"].read_job(job_id=scene["job_id"])).current_run


class TestHappyPath:
    def test_adopting_two_candidates_advances_everything_together(
        self, scene: dict[str, Any]
    ) -> None:
        first, first_receipt = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        second, second_receipt = confirmed(scene, key="c2", field_key=PARK_KEY, value="87")

        result = adopt(scene, (first.candidate_id, second.candidate_id))

        record = result.record
        assert record.from_revision == scene["run"].revision.revision_id
        assert record.to_revision != record.from_revision
        assert record.case_id == CASE_ID
        assert record.actor == scene["person"].actor
        assert record.adopted_at == NOW
        assert record.receipt_ids == (first_receipt.receipt_id, second_receipt.receipt_id)
        # The job's current run advanced to the new revision, new run identity.
        run = current_run(scene)
        assert run.revision.revision_id == record.to_revision
        assert run.run_id != scene["run"].run_id
        assert run.revision.material_digest == scene["run"].revision.material_digest
        # The new snapshot is registered and carries the adopted entries.
        registered = scene["registry"].read(CASE_ID, record.to_revision)
        assert registered is not None
        assert registered.digest() == record.snapshot_digest
        distance = registered.entries[DISTANCE_KEY]
        assert (distance.state, distance.origin) == ("present", "human_confirmed")
        assert str(distance.value) == "342"
        assert distance.unit == "m"
        assert distance.trace == f"receipt:{first_receipt.receipt_id}"
        assert str(registered.entries[PARK_KEY].value) == "87"
        # Adopted keys left the gaps; unrelated gaps stay.
        assert set(registered.gaps) == {KEPT_GAP}
        # Candidates flipped to adopted and left the awaiting-adoption list.
        for candidate in (first, second):
            assert scene["candidate_store"].read(CASE_ID, candidate.candidate_id).status == (
                "adopted"
            )
        assert scene["candidate_store"].confirmed_unadopted(CASE_ID) == ()
        assert [outcome.outcome for outcome in result.outcomes] == ["adopted", "adopted"]
        assert result.outcomes[0].confirmed_revision == first_receipt.expected_revision

    def test_second_adoption_chains_from_the_new_revision(self, scene: dict[str, Any]) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        second, _ = confirmed(scene, key="c2", field_key=PARK_KEY, value="87")
        first_result = adopt(scene, (first.candidate_id,), key="adopt-1")

        second_result = adopt(
            scene,
            (second.candidate_id,),
            key="adopt-2",
            expected=first_result.record.to_revision,
        )

        assert second_result.record.from_revision == first_result.record.to_revision
        chained = scene["registry"].read(CASE_ID, second_result.record.to_revision)
        assert str(chained.entries[DISTANCE_KEY].value) == "342"
        assert str(chained.entries[PARK_KEY].value) == "87"
        assert current_run(scene).revision.revision_id == second_result.record.to_revision


class TestIdempotentReplay:
    def test_replaying_the_same_key_returns_the_original_record(
        self, scene: dict[str, Any]
    ) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        original = adopt(scene, (first.candidate_id,))

        replayed = adopt(scene, (first.candidate_id,))

        assert replayed == original
        # No second advance happened: the job still sits on the first target revision.
        assert current_run(scene).revision.revision_id == original.record.to_revision

    def test_same_key_with_a_different_payload_conflicts(self, scene: dict[str, Any]) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        second, _ = confirmed(scene, key="c2", field_key=PARK_KEY, value="87")
        adopt(scene, (first.candidate_id,))

        with pytest.raises(ServiceFault) as fault:
            adopt(scene, (second.candidate_id,), key="adopt-1")

        assert fault.value.problem.code == ServiceErrorCode.CONFLICT


class TestConflicts:
    def test_stale_expected_revision_conflicts_and_changes_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        before = current_run(scene)

        with pytest.raises(ServiceFault) as fault:
            adopt(scene, (first.candidate_id,), expected="someone-elses-revision")

        assert fault.value.problem.code == ServiceErrorCode.CONFLICT
        assert current_run(scene) == before
        assert scene["candidate_store"].read(CASE_ID, first.candidate_id).status == "confirmed"
        assert scene["hook_inputs"] == []

    def test_missing_base_snapshot_conflicts(self, tmp_path: Path) -> None:
        bare = make_scene(tmp_path, register_base=False)
        first, _ = confirmed(bare, key="c1", field_key=DISTANCE_KEY, value="342")

        with pytest.raises(ServiceFault) as fault:
            adopt(bare, (first.candidate_id,))

        assert fault.value.problem.code == ServiceErrorCode.CONFLICT

    def test_terminal_job_refuses_adoption(self, scene: dict[str, Any]) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        asyncio.run(scene["store"].cancel(job_id=scene["job_id"], now=2000))

        with pytest.raises(ServiceFault) as fault:
            adopt(scene, (first.candidate_id,))

        assert fault.value.problem.code == ServiceErrorCode.CONFLICT
        assert scene["candidate_store"].read(CASE_ID, first.candidate_id).status == "confirmed"

    def test_store_level_cas_persists_nothing_on_a_lost_race(self, scene: dict[str, Any]) -> None:
        first, receipt = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        good = adopt(scene, (first.candidate_id,))
        # Rebuild the same-shaped commit as if it had validated against a revision
        # that is no longer current: the transaction must refuse and persist nothing.
        stale = AdoptionResult.model_validate(
            good.model_dump(mode="json")
            | {
                "record": good.record.model_dump(mode="json")
                | {
                    "adoption_id": "race-adoption",
                    "idempotency_key": "race-key",
                    "from_revision": "no-longer-current",
                    "to_revision": "race-target",
                },
                "new_revision": {
                    "case_id": CASE_ID,
                    "revision_id": "race-target",
                    "material_digest": scene["run"].revision.material_digest,
                },
            }
        )
        snapshot = CalculationSnapshot.model_validate(
            base_snapshot(scene["run"]).model_dump(mode="json")
            | {"revision": stale.new_revision.model_dump(mode="json")}
        )
        adopted = FactCandidate.model_validate(
            first.model_dump(mode="json") | {"status": "adopted"}
        )
        before = current_run(scene)

        with pytest.raises(ServiceFault) as fault:
            asyncio.run(
                scene["adoption_store"].commit(
                    stale,
                    (adopted,),
                    snapshot=snapshot,
                    new_run_id=uuid4(),
                    payload_digest="0" * 64,
                )
            )

        assert fault.value.problem.code == ServiceErrorCode.CONFLICT
        assert current_run(scene) == before
        assert scene["registry"].read(CASE_ID, "race-target") is None
        assert (
            scene["adoption_store"].replay(
                job_id=scene["job_id"],
                actor_id=scene["person"].actor.actor_id,
                idempotency_key="race-key",
                payload_digest="0" * 64,
            )
            is None
        )
        assert receipt.decision == "accept"


class TestBatchAtomicity:
    def test_a_rejected_candidate_fails_the_whole_batch_naming_it(
        self, scene: dict[str, Any]
    ) -> None:
        good, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        bad, _ = confirmed(scene, key="c2", field_key=PARK_KEY, value="87", decision="reject")
        before = current_run(scene)

        with pytest.raises(AdoptionRefusal) as refusal:
            adopt(scene, (good.candidate_id, bad.candidate_id))

        assert refusal.value.problem.code == ServiceErrorCode.VALIDATION
        assert refusal.value.candidate_id == bad.candidate_id
        assert refusal.value.reason == "not_confirmed"
        # Atomic no-op: the good candidate did NOT show up as adopted anywhere.
        assert scene["candidate_store"].read(CASE_ID, good.candidate_id).status == "confirmed"
        assert current_run(scene) == before
        assert scene["hook_inputs"] == []

    def test_pending_and_unknown_candidates_are_refused_by_id(self, scene: dict[str, Any]) -> None:
        pending = asyncio.run(
            scene["candidate_service"].register_candidate(
                scene["person"], CASE_ID, register_command("c1", DISTANCE_KEY, "342")
            )
        )

        with pytest.raises(AdoptionRefusal) as still_pending:
            adopt(scene, (pending.candidate_id,))
        assert still_pending.value.reason == "not_confirmed"

        with pytest.raises(AdoptionRefusal) as unknown:
            adopt(scene, ("no-such-candidate",))
        assert unknown.value.reason == "unknown_or_foreign_candidate"
        assert unknown.value.candidate_id == "no-such-candidate"

    def test_two_candidates_for_one_field_key_are_refused(self, scene: dict[str, Any]) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        second, _ = confirmed(scene, key="c2", field_key=DISTANCE_KEY, value="343")

        with pytest.raises(AdoptionRefusal) as refusal:
            adopt(scene, (first.candidate_id, second.candidate_id))

        assert refusal.value.reason == "duplicate_field_key"
        assert refusal.value.candidate_id == second.candidate_id


class TestAuthority:
    def test_a_non_human_actor_is_refused(self, scene: dict[str, Any]) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        model = person(actor=ActorReference(actor_id=scene["person"].actor.actor_id, kind="model"))

        with pytest.raises(ServiceFault) as fault:
            adopt(scene, (first.candidate_id,), actor=model)

        assert fault.value.problem.code == ServiceErrorCode.UNAUTHORIZED
        assert scene["candidate_store"].read(CASE_ID, first.candidate_id).status == "confirmed"

    def test_missing_correction_permission_is_refused(self, scene: dict[str, Any]) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        reviewer = person(permissions=frozenset({Permission.REVIEW, Permission.CONFIRM}))

        with pytest.raises(ServiceFault) as fault:
            adopt(scene, (first.candidate_id,), actor=reviewer)

        assert fault.value.problem.code == ServiceErrorCode.UNAUTHORIZED


class TestRecomputeHook:
    def test_hook_receives_the_merged_snapshot_and_its_output_is_registered(
        self, scene: dict[str, Any]
    ) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")

        def refresh(snapshot: CalculationSnapshot) -> CalculationSnapshot:
            data = snapshot.model_dump(mode="json")
            data["entries"]["table_5.case.derived"] = {
                "state": "present",
                "value": "684",
                "unit": "m",
                "origin": "computed",
                "trace": f"2 * {DISTANCE_KEY}",
            }
            return CalculationSnapshot.model_validate(data)

        scene["recompute_override"] = refresh
        result = adopt(scene, (first.candidate_id,))

        # The hook saw the merged snapshot: new revision, adopted entry present.
        assert len(scene["hook_inputs"]) == 1
        seen = scene["hook_inputs"][0]
        assert seen.revision == result.new_revision
        assert str(seen.entries[DISTANCE_KEY].value) == "342"
        # What the hook returned is exactly what got registered and digested.
        registered = scene["registry"].read(CASE_ID, result.record.to_revision)
        assert str(registered.entries["table_5.case.derived"].value) == "684"
        assert registered.digest() == result.record.snapshot_digest

    def test_a_hook_that_rewrites_adopted_values_is_refused(self, scene: dict[str, Any]) -> None:
        first, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        before = current_run(scene)

        def rogue(snapshot: CalculationSnapshot) -> CalculationSnapshot:
            data = snapshot.model_dump(mode="json")
            data["entries"][DISTANCE_KEY]["value"] = "9999"
            return CalculationSnapshot.model_validate(data)

        scene["recompute_override"] = rogue
        with pytest.raises(ServiceFault) as fault:
            adopt(scene, (first.candidate_id,))

        assert fault.value.problem.code == ServiceErrorCode.EXECUTION
        assert current_run(scene) == before
        assert scene["candidate_store"].read(CASE_ID, first.candidate_id).status == "confirmed"


class Resolver:
    def __init__(self, value: Principal) -> None:
        self.value = value

    async def current_principal(self) -> Principal:
        return self.value


def build_app(service: FactAdoptionService | None, resolver: object) -> FastAPI:
    app = FastAPI()
    app.state.fact_adoption = service
    app.state.principal_resolver = resolver
    app.include_router(fact_adoption_router)

    @app.exception_handler(ServiceFault)
    async def service_fault(request: Request, fault: ServiceFault) -> JSONResponse:
        return JSONResponse(
            status_code=FAULT_STATUS[fault.problem.code],
            content=fault.problem.model_dump(mode="json"),
        )

    return app


class TestRoutes:
    def test_post_adoptions_round_trip(self, scene: dict[str, Any]) -> None:
        first, first_receipt = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342")
        client = TestClient(build_app(scene["service"], Resolver(scene["person"])))

        response = client.post(
            f"/v1/review-jobs/{scene['job_id']}/adoptions",
            json={
                "idempotency_key": "adopt-http-1",
                "expected_revision": scene["run"].revision.revision_id,
                "candidate_ids": [first.candidate_id],
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["record"]["job_id"] == str(scene["job_id"])
        assert body["record"]["receipt_ids"] == [first_receipt.receipt_id]
        assert body["new_revision"]["revision_id"] == body["record"]["to_revision"]
        assert body["outcomes"][0]["outcome"] == "adopted"
        assert UUID(body["record"]["job_id"]) == scene["job_id"]

    def test_a_refused_candidate_answers_the_sanitized_envelope(
        self, scene: dict[str, Any]
    ) -> None:
        bad, _ = confirmed(scene, key="c1", field_key=DISTANCE_KEY, value="342", decision="reject")
        client = TestClient(build_app(scene["service"], Resolver(scene["person"])))

        response = client.post(
            f"/v1/review-jobs/{scene['job_id']}/adoptions",
            json={
                "idempotency_key": "adopt-http-1",
                "expected_revision": scene["run"].revision.revision_id,
                "candidate_ids": [bad.candidate_id],
            },
        )

        assert response.status_code == 422
        assert response.json() == {
            "schema_version": "service-v1",
            "code": "invalid_request",
            "message": "Service operation could not be completed.",
        }

    def test_an_unwired_plane_reports_capability_unavailable(self, scene: dict[str, Any]) -> None:
        client = TestClient(build_app(None, Resolver(scene["person"])))

        response = client.post(
            f"/v1/review-jobs/{uuid4()}/adoptions",
            json={
                "idempotency_key": "adopt-http-1",
                "expected_revision": "any",
                "candidate_ids": ["c1"],
            },
        )

        assert response.status_code == 503
        assert response.json()["code"] == "capability_unavailable"
