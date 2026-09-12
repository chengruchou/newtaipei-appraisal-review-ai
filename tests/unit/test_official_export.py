"""Export operations deliver exactly one chosen format, with per-table honesty.

These tests are the contract E implements and D consumes. They pin the three things that
would otherwise rot: a format is never "both", a workbook is not a PDF with missing fields,
and "succeeded" means the chosen format is complete rather than partially delivered.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from appraisal_review.domain.artifact_publication import ArtifactKey
from appraisal_review.domain.official_export import (
    REQUIRED_TABLES,
    ConvertedPDFArtifact,
    ExportOperation,
    ExportRequest,
    TableOutcome,
    TemplateBundleReference,
    WorkbookArtifact,
)
from appraisal_review.domain.service_contracts import (
    RevisionReference,
    RunReference,
    ServiceErrorCode,
    ServiceProblem,
)

CASE = "shulin-001"
SNAPSHOT = "a" * 64
BUNDLE = TemplateBundleReference(bundle_id="official-2026", version="v1", bundle_hash="b" * 64)
TABLES = ("table_3", "table_4", "table_5")


def run(attempt: int = 1) -> RunReference:
    return RunReference(
        run_id=UUID(int=10),
        revision=RevisionReference(case_id=CASE, revision_id="r1", material_digest="c" * 64),
        attempt_id=UUID(int=attempt),
    )


def key(artifact_id: UUID, suffix: str) -> str:
    return ArtifactKey.for_run(run(), artifact_id, suffix=suffix).key()  # type: ignore[arg-type]


def workbook(n: int, table: str = "table_3", **over: Any) -> WorkbookArtifact:
    artifact_id = UUID(int=100 + n)
    base: dict[str, Any] = dict(
        artifact_id=artifact_id,
        key=key(artifact_id, "xlsx"),
        table=table,
        content_hash=f"{n:064d}",
        size_bytes=228897,
        filename=f"{artifact_id}.xlsx",
        template_bundle=BUNDLE,
        template_hash="d" * 64,
        snapshot_digest=SNAPSHOT,
        writer_version="1",
        sheet_name="表3區段勘查表",
        written_range="A1:V46",
        written_cells=("C5", "D7"),
    )
    return WorkbookArtifact(**{**base, **over})


def pdf(n: int, table: str = "table_3", **over: Any) -> ConvertedPDFArtifact:
    artifact_id = UUID(int=200 + n)
    base: dict[str, Any] = dict(
        artifact_id=artifact_id,
        key=key(artifact_id, "pdf"),
        table=table,
        content_hash=f"{n:064d}",
        size_bytes=18127,
        filename=f"{artifact_id}.pdf",
        template_bundle=BUNDLE,
        template_hash="d" * 64,
        snapshot_digest=SNAPSHOT,
        writer_version="1",
        source_workbook_hash="e" * 64,
        page_count=2,
    )
    return ConvertedPDFArtifact(**{**base, **over})


def operation(fmt: str, artifacts: tuple[Any, ...], status: str, **over: Any) -> ExportOperation:
    base: dict[str, Any] = dict(
        export_id=UUID(int=1),
        job_id=UUID(int=2),
        run=run(),
        export_format=fmt,
        requested_mode="draft",
        effective_mode="draft",
        status=status,
        payload_digest="f" * 64,
        calculation_snapshot_digest=SNAPSHOT,
        template_bundle=BUNDLE,
        tables=tuple(
            TableOutcome(table=a.table, delivered=True, artifact_id=a.artifact_id)
            for a in artifacts
        ),
        artifacts=artifacts,
    )
    return ExportOperation(**{**base, **over})


class TestFormatIsSingleValued:
    def test_unknown_format_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ExportRequest(
                idempotency_key="k1",
                run=run(),
                calculation_snapshot_digest=SNAPSHOT,
                template_bundle=BUNDLE,
                requested_mode="draft",
                export_format="both",  # type: ignore[arg-type]
            )

    def test_format_changes_the_payload_digest(self) -> None:
        # The 409 for "same key, different format" is derived from this, so if the digest
        # ignored format the two requests would look like one idempotent replay.
        common: dict[str, Any] = dict(
            idempotency_key="k1",
            run=run(),
            calculation_snapshot_digest=SNAPSHOT,
            template_bundle=BUNDLE,
            requested_mode="draft",
        )
        as_pdf = ExportRequest(**common, export_format="pdf")
        as_xlsx = ExportRequest(**common, export_format="xlsx")
        assert as_pdf.payload_digest() != as_xlsx.payload_digest()

    def test_identical_requests_replay_to_one_digest(self) -> None:
        common: dict[str, Any] = dict(
            idempotency_key="k1",
            run=run(),
            calculation_snapshot_digest=SNAPSHOT,
            template_bundle=BUNDLE,
            requested_mode="draft",
            export_format="xlsx",
        )
        assert ExportRequest(**common).payload_digest() == ExportRequest(**common).payload_digest()


class TestArtifactUnionKeepsFormatsApart:
    def test_workbook_cannot_borrow_pdf_fields(self) -> None:
        for field in ("page_count", "font_hash", "source_workbook_hash"):
            with pytest.raises(ValidationError):
                workbook(1, **{field: 2 if field == "page_count" else "e" * 64})

    def test_pdf_requires_the_workbook_it_came_from(self) -> None:
        with pytest.raises(ValidationError):
            ConvertedPDFArtifact(
                artifact_id=UUID(int=300),
                key=key(UUID(int=300), "pdf"),
                table="table_3",
                content_hash="0" * 64,
                size_bytes=1,
                filename=f"{UUID(int=300)}.pdf",
                template_bundle=BUNDLE,
                template_hash="d" * 64,
                snapshot_digest=SNAPSHOT,
                writer_version="1",
                page_count=2,
            )

    def test_pdf_cannot_borrow_workbook_fields(self) -> None:
        with pytest.raises(ValidationError):
            pdf(1, sheet_name="表3區段勘查表")

    def test_each_kind_reports_its_own_format(self) -> None:
        assert workbook(1).export_format == "xlsx"
        assert pdf(1).export_format == "pdf"

    def test_media_types_are_fixed_per_kind(self) -> None:
        assert workbook(1).content_type.endswith("spreadsheetml.sheet")
        assert pdf(1).content_type == "application/pdf"


class TestBytesNameAndKeyAgree:
    def test_key_suffix_must_match_the_kind(self) -> None:
        wrong = UUID(int=101)
        with pytest.raises(ValidationError, match=r"must use a \.pdf key"):
            workbook(1, key=key(wrong, "pdf"), artifact_id=wrong, filename=f"{wrong}.xlsx")

    def test_filename_extension_must_match(self) -> None:
        # A .pdf name on workbook bytes is how a reviewer gets a file Excel refuses to open.
        with pytest.raises(ValidationError, match=r"must end with \.xlsx"):
            workbook(1, filename=f"{UUID(int=101)}.pdf")

    def test_filename_cannot_contain_a_separator(self) -> None:
        with pytest.raises(ValidationError, match="path separators"):
            workbook(1, filename="../escape.xlsx")

    def test_key_identity_must_match_the_artifact(self) -> None:
        with pytest.raises(ValidationError, match="must match"):
            workbook(1, key=key(UUID(int=999), "xlsx"))


class TestOperationHonesty:
    def test_a_pdf_export_cannot_deliver_a_workbook(self) -> None:
        # The filled workbook is a traceable intermediate, never a delivered PDF table.
        with pytest.raises(ValidationError, match="cannot deliver"):
            operation("pdf", (workbook(1),), "partial")

    def test_succeeded_requires_all_three_tables(self) -> None:
        with pytest.raises(ValidationError, match="all three official tables"):
            operation("xlsx", (workbook(1, "table_3"),), "succeeded")

    def test_succeeded_with_all_three_tables_is_valid(self) -> None:
        complete = tuple(workbook(i, t) for i, t in enumerate(TABLES))
        assert operation("xlsx", complete, "succeeded").missing_tables() == frozenset()

    def test_succeeded_carries_no_blocker(self) -> None:
        complete = tuple(workbook(i, t) for i, t in enumerate(TABLES))
        with pytest.raises(ValidationError, match="no problem or blocker"):
            operation("xlsx", complete, "succeeded", blockers=("missing lot area",))

    def test_partial_names_what_is_missing(self) -> None:
        op = operation("xlsx", (workbook(0, "table_3"), workbook(1, "table_5")), "partial")
        assert op.missing_tables() == frozenset({"table_4"})

    def test_partial_cannot_be_complete(self) -> None:
        complete = tuple(workbook(i, t) for i, t in enumerate(TABLES))
        with pytest.raises(ValidationError, match="some but not all"):
            operation("xlsx", complete, "partial")

    def test_unfinished_export_delivers_nothing(self) -> None:
        for status in ("queued", "running"):
            with pytest.raises(ValidationError, match="delivers nothing yet"):
                operation("xlsx", (workbook(1),), status)

    def test_failed_export_delivers_nothing(self) -> None:
        with pytest.raises(ValidationError, match="delivers nothing"):
            operation("xlsx", (workbook(1),), "failed")

    def test_artifacts_must_come_from_the_operation_snapshot(self) -> None:
        # A file filled from a superseded snapshot is exactly the stale-number failure the
        # whole revision machinery exists to prevent.
        with pytest.raises(ValidationError, match="exact snapshot"):
            operation("xlsx", (workbook(1, snapshot_digest="9" * 64),), "partial")

    def test_delivered_tables_and_artifacts_must_agree(self) -> None:
        with pytest.raises(ValidationError, match="must agree"):
            operation(
                "xlsx",
                (workbook(1),),
                "partial",
                tables=(TableOutcome(table="table_3", delivered=True, artifact_id=UUID(int=777)),),
            )

    def test_one_outcome_per_table(self) -> None:
        with pytest.raises(ValidationError, match="at most once"):
            operation(
                "xlsx",
                (workbook(0, "table_3"), workbook(1, "table_3")),
                "partial",
            )


class TestFormalIsNeverPromoted:
    def test_formal_output_requires_a_formal_request(self) -> None:
        complete = tuple(workbook(i, t) for i, t in enumerate(TABLES))
        with pytest.raises(ValidationError, match="requires a formal request"):
            operation(
                "xlsx", complete, "succeeded", requested_mode="draft", effective_mode="formal"
            )

    def test_formal_output_cannot_carry_blockers(self) -> None:
        complete = tuple(workbook(i, t) for i, t in enumerate(TABLES))
        with pytest.raises(ValidationError):
            operation(
                "xlsx",
                complete,
                "partial",
                requested_mode="formal",
                effective_mode="formal",
                blockers=("awaiting reviewer approval",),
            )

    def test_a_formal_request_may_stay_a_draft_with_blockers(self) -> None:
        # Downgrading is the honest answer when prerequisites are unmet.
        op = operation(
            "xlsx",
            (workbook(0, "table_3"),),
            "partial",
            requested_mode="formal",
            effective_mode="draft",
            blockers=("lot area missing",),
        )
        assert op.effective_mode == "draft"
        assert op.blockers == ("lot area missing",)


class TestUndeliveredTablesExplainThemselves:
    def test_an_undelivered_table_may_carry_a_problem(self) -> None:
        outcome = TableOutcome(
            table="table_4",
            delivered=False,
            problem=ServiceProblem(code=ServiceErrorCode.VALIDATION),
        )
        assert outcome.artifact_id is None

    def test_an_undelivered_table_cannot_name_an_artifact(self) -> None:
        with pytest.raises(ValidationError, match="cannot name an artifact"):
            TableOutcome(table="table_4", delivered=False, artifact_id=UUID(int=1))

    def test_a_delivered_table_must_name_its_artifact(self) -> None:
        with pytest.raises(ValidationError, match="must name its artifact"):
            TableOutcome(table="table_4", delivered=True)


def test_required_tables_are_the_three_official_ones() -> None:
    assert frozenset(TABLES) == REQUIRED_TABLES
