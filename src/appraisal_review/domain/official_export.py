"""Official table export operations: one requested format, per-table outcomes.

A reviewer asks for the three official tables in exactly one format. `xlsx` fills the
official workbook from a fixed calculation snapshot and delivers it. `pdf` fills that same
workbook and converts those exact bytes, so the PDF names the workbook it came from and the
workbook stays an internal intermediate rather than a second download.

Two confusions this module refuses to represent:

- A format is never "both". The chosen format is part of the idempotent payload, so the
  same key with a different format is a conflict rather than a second namespace. Changing
  format is a new operation with a new key.
- A workbook is not a PDF with missing fields. The two artifacts are a discriminated union,
  so an `xlsx` entry cannot borrow `page_count`, `font_hash` or a PDF's page verification,
  and a `pdf` entry cannot omit the workbook hash that makes it traceable.

Export status is independent of the case's review status: a reviewer may download a draft
for checking long before the case could ever be formally completed.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from appraisal_review.domain.artifact_publication import ArtifactKey, ArtifactSuffix
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.official_table_mapping import OfficialTable
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    OpaqueID,
    RunReference,
    ServiceModel,
    ServiceProblem,
)

ExportFormat = Literal["pdf", "xlsx"]
ExportMode = Literal["draft", "formal"]
ExportStatus = Literal["queued", "running", "succeeded", "failed", "partial"]

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MEDIA_TYPE = "application/pdf"

#: The three official tables a complete export delivers, whichever format was chosen.
REQUIRED_TABLES: frozenset[OfficialTable] = frozenset({"table_3", "table_4", "table_5"})

_SUFFIX: dict[ExportFormat, ArtifactSuffix] = {"pdf": "pdf", "xlsx": "xlsx"}


class TemplateBundleReference(ServiceModel):
    """A pinned official asset bundle. A request names it; it never carries a path."""

    bundle_id: OpaqueID
    version: OpaqueID
    bundle_hash: Digest


class ExportBasis(ServiceModel):
    """Everything a client needs to compose a valid export request, served, not guessed.

    The page must not invent the snapshot digest or the template bundle: both identify
    server-held state, and a wrong guess turns into a confusing conflict. This view hands
    them over for the job's current run, together with the snapshot's open gaps so the
    format chooser can show what a draft will still be missing.
    """

    job_id: UUID
    run: RunReference
    calculation_snapshot_digest: Digest
    template_bundle: TemplateBundleReference
    blockers: tuple[str, ...] = ()


class ExportRequest(ServiceModel):
    """An untrusted command. Eligibility for formal output is decided by the server."""

    idempotency_key: OpaqueID
    run: RunReference
    calculation_snapshot_digest: Digest
    template_bundle: TemplateBundleReference
    requested_mode: ExportMode
    export_format: ExportFormat

    def payload_digest(self) -> str:
        """Canonical digest covering the format, so a format change cannot reuse a key."""
        return content_digest(self)


class _ExportArtifact(ServiceModel):
    """Facts every delivered file carries, independent of its format."""

    artifact_id: UUID
    key: str
    table: OfficialTable
    content_hash: Digest
    size_bytes: int = Field(ge=1, strict=True)
    filename: str = Field(min_length=1, max_length=255)
    object_version: str | None = Field(default=None, min_length=1)
    template_bundle: TemplateBundleReference
    template_hash: Digest
    snapshot_digest: Digest
    writer_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_key_and_filename(self) -> _ExportArtifact:
        parsed = ArtifactKey.parse(self.key)
        if parsed.artifact_id != self.artifact_id:
            raise ValueError("Artifact key and artifact identity must match")
        if parsed.suffix != _SUFFIX[self.export_format]:
            raise ValueError(f"A {self.export_format} artifact must use a .{parsed.suffix} key")
        if "/" in self.filename or "\\" in self.filename:
            raise ValueError("A delivered filename must not contain path separators")
        if not self.filename.endswith(f".{parsed.suffix}"):
            # The name reaches Content-Disposition; a mismatched extension is how a reviewer
            # ends up with a .pdf that Excel refuses and nobody can explain.
            raise ValueError(f"A {self.export_format} filename must end with .{parsed.suffix}")
        return self

    @property
    def export_format(self) -> ExportFormat:
        raise NotImplementedError


class WorkbookArtifact(_ExportArtifact):
    """A filled copy of the official workbook, with the cells the writer was allowed."""

    kind: Literal["official_workbook"] = "official_workbook"
    content_type: Literal["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    sheet_name: str = Field(min_length=1, max_length=255)
    written_range: str = Field(min_length=3, max_length=32)
    written_cells: tuple[str, ...] = Field(min_length=1)
    verification: Literal["workbook_reopened"] = "workbook_reopened"

    @property
    def export_format(self) -> ExportFormat:
        return "xlsx"

    @model_validator(mode="after")
    def unique_written_cells(self) -> WorkbookArtifact:
        if len(set(self.written_cells)) != len(self.written_cells):
            raise ValueError("Written cells must be unique")
        return self


class ConvertedPDFArtifact(_ExportArtifact):
    """A PDF converted from one exact workbook copy, which it names."""

    kind: Literal["converted_pdf"] = "converted_pdf"
    content_type: Literal["application/pdf"] = "application/pdf"
    # Nothing inside a PDF proves which workbook produced it, so the hash is recorded
    # provenance the conversion step asserts and the operation checks for consistency.
    source_workbook_hash: Digest
    #: The exact bytes rasterized: a render-only derived copy of the filled workbook with
    #: hidden legacy sheets removed, per the organizer's print-setup-on-derived-copy rule.
    #: None means the workbook itself was rasterized unmodified.
    render_input_hash: Digest | None = None
    page_count: int = Field(ge=1, strict=True)
    font_hash: Digest | None = None
    verification: Literal["converted_pdf_reopened"] = "converted_pdf_reopened"

    @property
    def export_format(self) -> ExportFormat:
        return "pdf"


ExportArtifact = Annotated[WorkbookArtifact | ConvertedPDFArtifact, Field(discriminator="kind")]


class TableOutcome(ServiceModel):
    """Per-table result, so a partial export says which table is missing and why."""

    table: OfficialTable
    delivered: bool
    artifact_id: UUID | None = None
    problem: ServiceProblem | None = None

    @model_validator(mode="after")
    def delivered_or_explained(self) -> TableOutcome:
        if self.delivered and self.artifact_id is None:
            raise ValueError("A delivered table must name its artifact")
        if not self.delivered and self.artifact_id is not None:
            raise ValueError("An undelivered table cannot name an artifact")
        return self


class ExportOperation(ServiceModel):
    """Immutable format plus the current state of one export request."""

    export_id: UUID
    job_id: UUID
    run: RunReference
    export_format: ExportFormat
    requested_mode: ExportMode
    # What the server was actually willing to produce. A formal request whose prerequisites
    # are unmet stays draft and lists blockers; a draft is never silently promoted.
    effective_mode: ExportMode
    status: ExportStatus
    payload_digest: Digest
    calculation_snapshot_digest: Digest
    template_bundle: TemplateBundleReference
    #: The approval that authorizes a formal delivery; None for drafts. Publication
    #: and the content route re-check its live status - the id is a reference, not proof.
    approval_id: UUID | None = None
    tables: tuple[TableOutcome, ...] = ()
    artifacts: tuple[ExportArtifact, ...] = ()
    blockers: tuple[str, ...] = ()
    problem: ServiceProblem | None = None

    @model_validator(mode="after")
    def coherent_outcome(self) -> ExportOperation:
        if self.effective_mode == "formal" and self.requested_mode != "formal":
            raise ValueError("Formal output requires a formal request")
        if self.effective_mode == "formal" and self.blockers:
            raise ValueError("Formal output cannot carry unresolved blockers")
        if self.effective_mode == "formal" and self.approval_id is None:
            raise ValueError("Formal output names the approval that authorizes it")

        for artifact in self.artifacts:
            if artifact.export_format != self.export_format:
                raise ValueError(
                    f"A {self.export_format} export cannot deliver a {artifact.kind} artifact"
                )
            if artifact.snapshot_digest != self.calculation_snapshot_digest:
                raise ValueError("Every artifact must come from the operation's exact snapshot")

        delivered = {outcome.table for outcome in self.tables if outcome.delivered}
        named = {outcome.artifact_id for outcome in self.tables if outcome.delivered}
        if len({outcome.table for outcome in self.tables}) != len(self.tables):
            raise ValueError("Each table appears at most once")
        if named != {artifact.artifact_id for artifact in self.artifacts}:
            raise ValueError("Delivered tables and artifacts must agree")

        if self.status == "succeeded":
            # Success means the chosen format is complete. Waiting on the other format, or
            # counting an internal workbook towards a PDF export, is how a partial delivery
            # gets reported as done.
            if delivered != REQUIRED_TABLES:
                raise ValueError("A succeeded export delivers all three official tables")
            if self.problem is not None or self.blockers:
                raise ValueError("A succeeded export carries no problem or blocker")
        if self.status == "partial" and not (delivered and delivered != REQUIRED_TABLES):
            raise ValueError("A partial export delivers some but not all tables")
        if self.status in {"queued", "running"} and self.artifacts:
            raise ValueError("An unfinished export delivers nothing yet")
        if self.status == "failed" and delivered:
            raise ValueError("A failed export delivers nothing")
        return self

    def missing_tables(self) -> frozenset[OfficialTable]:
        return REQUIRED_TABLES - {outcome.table for outcome in self.tables if outcome.delivered}
