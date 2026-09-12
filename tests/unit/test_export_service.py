"""Export operations: exact replay, format conflicts, per-table honesty, durability.

The fakes here stand in for the workbook filler and converter so the service semantics
are pinned independently of the real writer: what gets accepted, what replays, what
conflicts, and what an operation may claim about its own outcome.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from appraisal_review.adapters.local.export_store import SQLiteExportStore
from appraisal_review.adapters.local.sqlite_review_store import SQLiteReviewStore
from appraisal_review.application.exports import ExportAssets, ExportService
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.calculation_snapshot import (
    CalculationSnapshot,
    SnapshotEntry,
    SnapshotSubject,
)
from appraisal_review.domain.official_export import ExportRequest
from appraisal_review.domain.official_table_mapping import TableMapping
from appraisal_review.domain.service_contracts import Permission, RunReference
from appraisal_review.ports.workbook_conversion import ConversionUnavailable, ConvertedWorkbook
from appraisal_review.testing.job_store_contract import principal, submission

TABLES = ("table_3", "table_4", "table_5")


@dataclass
class FakeFilled:
    content: bytes
    sheet_name: str
    written_cells: tuple[str, ...]


class FakeConverter:
    def __init__(self) -> None:
        self.unavailable = False
        self.calls: list[str] = []

    def convert(
        self,
        workbook: bytes,
        *,
        expected_sha256: str,
        verify_text: tuple[str, ...] = (),
        expected_page_count: int | None = None,
    ) -> ConvertedWorkbook:
        assert hashlib.sha256(workbook).hexdigest() == expected_sha256
        self.calls.append(expected_sha256)
        if self.unavailable:
            raise ConversionUnavailable("converter_missing")
        content = b"%PDF-1.7 " + workbook[:12]
        return ConvertedWorkbook(
            content=content,
            page_count=1,
            workbook_sha256=expected_sha256,
            output_sha256=hashlib.sha256(content).hexdigest(),
            converter="fake-soffice",
            converter_version="0",
        )


class Snapshots:
    """Round-trips through JSON exactly like the on-disk registry, so a value that
    changes type across serialization fails here and not on a live workbench."""

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
                },
                {
                    "cell": "L1",
                    "source": f"{table}.case.survey_date",
                    "label": "survey date",
                    "value_kind": "date",
                },
            ],
        }
    )


@pytest.fixture
def scene(tmp_path: Path) -> dict[str, Any]:
    store = SQLiteReviewStore(tmp_path / "private" / "review.sqlite3")
    person = principal()
    request = submission()
    job_id, run_id = uuid4(), uuid4()
    asyncio.run(store.create_job(person, request, job_id=job_id, run_id=run_id, now=1000))
    record = asyncio.run(store.read_job(job_id=job_id))
    run = record.current_run
    templates: dict[str, Path] = {}
    mappings: dict[str, TableMapping] = {}
    for table in TABLES:
        path = tmp_path / f"{table}.xlsx"
        path.write_bytes(b"PK\x03\x04-template-" + table.encode())
        templates[table] = path
        mappings[table] = mapping(table, hashlib.sha256(path.read_bytes()).hexdigest())
    assets = ExportAssets.load(
        bundle_id="official-test", version="v1", templates=templates, mappings=mappings
    )
    snapshot = CalculationSnapshot(
        revision=run.revision,
        district="Shulin",
        valuation_date=date(2022, 9, 1),
        rule_bundle_id="shulin-2022",
        rule_bundle_version="v1",
        subjects=(
            SnapshotSubject(subject_id="P001", role="comparison_base", label="base"),
            SnapshotSubject(subject_id="P002", role="comparable", label="c1"),
        ),
        entries={
            key: entry
            for table in TABLES
            for key, entry in {
                f"{table}.case.example": SnapshotEntry(
                    state="present",
                    value=Decimal("5"),
                    unit="percentage_points",
                    origin="computed",
                    trace="matrix row excellent vs inferior",
                ),
                # A ROC-calendar date is numeric-looking text; it must stay text through
                # every serialization boundary or date cells refuse to render.
                f"{table}.case.survey_date": SnapshotEntry(
                    state="present",
                    value="1110901",
                    origin="given_input",
                    trace="assignment cover page",
                ),
            }.items()
        },
    )
    filled: dict[str, int] = {"count": 0}

    def filler(
        template: bytes, table_mapping: TableMapping, snap: CalculationSnapshot
    ) -> FakeFilled:
        filled["count"] += 1
        return FakeFilled(
            content=b"PK\x03\x04-filled-" + table_mapping.table.encode(),
            sheet_name=table_mapping.sheet_name,
            written_cells=tuple(binding.cell for binding in table_mapping.bindings),
        )

    converter = FakeConverter()
    export_store = SQLiteExportStore(store)
    service = ExportService(
        jobs=ReviewJobService(store, store.results, policy=store.policy),
        store=export_store,
        assets=assets,
        filler=filler,
        converter=converter,
        snapshots=Snapshots(snapshot),
    )
    return dict(
        service=service,
        store=store,
        export_store=export_store,
        person=person,
        job_id=job_id,
        run=run,
        assets=assets,
        snapshot=snapshot,
        converter=converter,
        filled=filled,
    )


def request_for(h: dict[str, Any], fmt: str, key: str = "k1", mode: str = "draft") -> ExportRequest:
    run: RunReference = h["run"]
    return ExportRequest(
        idempotency_key=key,
        run=RunReference(run_id=run.run_id, revision=run.revision),
        calculation_snapshot_digest=h["snapshot"].digest(),
        template_bundle=h["assets"].bundle,
        requested_mode=mode,
        export_format=fmt,
    )


def submit(h: dict[str, Any], fmt: str, key: str = "k1", mode: str = "draft") -> Any:
    return asyncio.run(
        h["service"].submit(h["person"], h["job_id"], request_for(h, fmt, key, mode))
    )


def run_pending(h: dict[str, Any]) -> None:
    asyncio.run(h["service"].run_pending())


def read(h: dict[str, Any], export_id: UUID) -> Any:
    return asyncio.run(h["service"].read(h["person"], h["job_id"], export_id))


class TestSubmitAndReplay:
    def test_exact_replay_returns_the_same_operation(self, scene: dict[str, Any]) -> None:
        first = submit(scene, "xlsx")
        again = submit(scene, "xlsx")
        assert first.export_id == again.export_id
        assert first.payload_digest == again.payload_digest

    def test_same_key_different_format_conflicts(self, scene: dict[str, Any]) -> None:
        submit(scene, "xlsx")
        with pytest.raises(ServiceFault):
            submit(scene, "pdf")

    def test_stale_snapshot_digest_conflicts(self, scene: dict[str, Any]) -> None:
        request = request_for(scene, "xlsx").model_copy(
            update={"calculation_snapshot_digest": "9" * 64}
        )
        with pytest.raises(ServiceFault):
            asyncio.run(scene["service"].submit(scene["person"], scene["job_id"], request))

    def test_wrong_template_bundle_is_invalid(self, scene: dict[str, Any]) -> None:
        request = request_for(scene, "xlsx")
        request = request.model_copy(
            update={
                "template_bundle": request.template_bundle.model_copy(
                    update={"bundle_hash": "8" * 64}
                )
            }
        )
        with pytest.raises(ServiceFault):
            asyncio.run(scene["service"].submit(scene["person"], scene["job_id"], request))

    def test_formal_request_is_refused_while_no_gate_exists(self, scene: dict[str, Any]) -> None:
        # The round has no formal-approval gate, and a draft must never be promoted to
        # stand in for one, so asking for formal output conflicts instead of downgrading.
        with pytest.raises(ServiceFault):
            submit(scene, "xlsx", mode="formal")

    def test_draft_with_snapshot_gaps_still_succeeds_and_lists_none(
        self, scene: dict[str, Any]
    ) -> None:
        gapped = type(scene["snapshot"]).model_validate(
            scene["snapshot"]
            .model_copy(
                update={"gaps": {"table_4.P002.example_gap": "not provided by the assignment"}}
            )
            .model_dump()
        )
        scene["service"].snapshots.snapshot = gapped
        scene["snapshot"] = gapped  # request_for pins the digest of what the server holds
        operation = submit(scene, "xlsx", key="k-gapped")
        run_pending(scene)
        done = read(scene, operation.export_id)
        # Gaps are content states rendered blank inside the sheets and listed by the
        # basis route; the operation itself delivered everything the format asked for.
        assert done.status == "succeeded"
        assert done.blockers == ()


class TestExecution:
    def test_xlsx_export_delivers_three_workbooks(self, scene: dict[str, Any]) -> None:
        operation = submit(scene, "xlsx")
        run_pending(scene)
        done = read(scene, operation.export_id)
        assert done.status == "succeeded"
        assert {a.kind for a in done.artifacts} == {"official_workbook"}
        assert len(done.artifacts) == 3
        assert scene["converter"].calls == [], "xlsx must not touch the converter"
        for artifact in done.artifacts:
            found = scene["export_store"].find_delivered(scene["job_id"], artifact.artifact_id)
            assert found is not None
            _, body = found
            assert hashlib.sha256(body).hexdigest() == artifact.content_hash

    def test_pdf_export_converts_the_same_filled_workbook(self, scene: dict[str, Any]) -> None:
        operation = submit(scene, "pdf")
        run_pending(scene)
        done = read(scene, operation.export_id)
        assert done.status == "succeeded"
        assert {a.kind for a in done.artifacts} == {"converted_pdf"}
        workbook_hashes = set(scene["converter"].calls)
        assert {a.source_workbook_hash for a in done.artifacts} == workbook_hashes

    def test_converter_missing_fails_pdf_but_not_the_record(self, scene: dict[str, Any]) -> None:
        scene["converter"].unavailable = True
        operation = submit(scene, "pdf")
        run_pending(scene)
        done = read(scene, operation.export_id)
        assert done.status == "failed"
        assert done.artifacts == ()
        assert all(not outcome.delivered for outcome in done.tables)

    def test_converter_missing_leaves_xlsx_export_untouched(self, scene: dict[str, Any]) -> None:
        scene["converter"].unavailable = True
        operation = submit(scene, "xlsx")
        run_pending(scene)
        assert read(scene, operation.export_id).status == "succeeded"

    def test_operation_survives_a_store_reopen(self, scene: dict[str, Any]) -> None:
        operation = submit(scene, "xlsx")
        run_pending(scene)
        reopened = SQLiteExportStore(scene["store"])
        stored = reopened.read(scene["job_id"], operation.export_id)
        assert stored is not None and stored.status == "succeeded"

    def test_execution_is_idempotent(self, scene: dict[str, Any]) -> None:
        operation = submit(scene, "xlsx")
        run_pending(scene)
        first = read(scene, operation.export_id)
        run_pending(scene)
        assert read(scene, operation.export_id) == first


class TestAuthorization:
    def test_a_stranger_cannot_submit_or_read(self, scene: dict[str, Any]) -> None:
        from dataclasses import replace

        stranger = replace(scene["person"], case_ids=frozenset())
        with pytest.raises(ServiceFault):
            asyncio.run(
                scene["service"].submit(stranger, scene["job_id"], request_for(scene, "xlsx"))
            )
        operation = submit(scene, "xlsx")
        with pytest.raises(ServiceFault):
            asyncio.run(scene["service"].read(stranger, scene["job_id"], operation.export_id))

    def test_review_permission_is_required(self, scene: dict[str, Any]) -> None:
        from dataclasses import replace

        limited = replace(
            scene["person"],
            permissions=frozenset(scene["person"].permissions) - {Permission.REVIEW},
        )
        with pytest.raises(ServiceFault):
            asyncio.run(
                scene["service"].submit(limited, scene["job_id"], request_for(scene, "xlsx"))
            )
