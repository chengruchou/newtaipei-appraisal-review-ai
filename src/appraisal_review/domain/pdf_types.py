"""PDF value types; public consumers import these through pdf_models.

Kept below factor_models in the dependency graph to avoid a circular import
between FactorReviewResult and AgentReviewRun.pdf_result.
"""

from __future__ import annotations

import posixpath
from enum import StrEnum
from typing import Annotated, ClassVar, Literal
from urllib.parse import unquote, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class PDFErrorCode(StrEnum):
    UNSUPPORTED_URI = "unsupported_document_uri"
    SOURCE_DESTINATION_CONFLICT = "source_destination_conflict"
    READ = "pdf_read_error"
    PLACEMENT = "pdf_field_placement_error"
    FONT = "pdf_font_error"
    WRITE = "pdf_write_error"
    INVALID_RESULT = "invalid_pdf_result"


class PDFWriteError(Exception):
    """Adapter failure. Exception details are never returned to clients."""

    code: ClassVar[PDFErrorCode] = PDFErrorCode.WRITE


class UnsupportedDocumentURIError(PDFWriteError, ValueError):
    code = PDFErrorCode.UNSUPPORTED_URI


class SourceDestinationConflictError(PDFWriteError, ValueError):
    code = PDFErrorCode.SOURCE_DESTINATION_CONFLICT


class PDFReadError(PDFWriteError):
    code = PDFErrorCode.READ


class PDFFieldPlacementError(PDFWriteError, ValueError):
    code = PDFErrorCode.PLACEMENT


class PDFFontError(PDFWriteError):
    code = PDFErrorCode.FONT


class InvalidPDFResultError(PDFWriteError):
    code = PDFErrorCode.INVALID_RESULT


def document_identity(uri: str) -> tuple[str, str, str]:
    """Validate a URI without I/O; file alias/inode checks belong to storage.

    S3 paths are literal object keys, not filesystem paths. Reject percent
    escapes for S3 to avoid competing interpretations across SDK adapters.
    """
    if not uri or any(ord(c) < 33 for c in uri):
        raise UnsupportedDocumentURIError("Invalid document URI")
    parts = urlsplit(uri)
    if parts.query or parts.fragment or "?" in uri or "#" in uri:
        raise UnsupportedDocumentURIError("Queries and fragments are unsupported")
    if parts.scheme == "file" and parts.netloc in {"", "localhost"}:
        path = unquote(parts.path)
        if not path.startswith("/") or path == "/" or "\x00" in path:
            raise UnsupportedDocumentURIError("An absolute file path is required")
        return ("file", "", posixpath.normpath(path))
    if parts.scheme == "s3" and parts.netloc and parts.path not in {"", "/"}:
        if any(c in parts.netloc for c in "@:%") or "%" in parts.path:
            raise UnsupportedDocumentURIError("Invalid S3 bucket or escaped key")
        return ("s3", parts.netloc, parts.path[1:])
    raise UnsupportedDocumentURIError("Only absolute file and S3 object URIs are supported")


class PDFModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class PDFValueRef(PDFModel):
    """Explicit binding; field_id is a placement ID, never an expression."""

    scope: Literal["regional", "individual"]
    target_id: Identifier
    comparable_id: Identifier
    value: Literal[
        "target_grade", "comparable_grade", "adjustment_percent", "total_adjustment_percent"
    ]
    factor_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_factor(self) -> PDFValueRef:
        if (self.value == "total_adjustment_percent") != (self.factor_id is None):
            raise ValueError("Only a total reference omits factor_id")
        return self


class PDFField(PDFModel):
    field_id: Identifier
    page: int = Field(ge=1, strict=True)
    bounding_box: tuple[float, float, float, float]
    max_characters: int | None = Field(default=None, ge=1, strict=True)
    value_ref: PDFValueRef | None = None
    operation: Literal["fill_blank", "annotate", "correct"] = "fill_blank"

    @field_validator("bounding_box")
    @classmethod
    def validate_box(
        cls, box: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = box
        if min(box) < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError("PDF boxes must be nonnegative with positive width and height")
        return box


class PDFFieldMap(PDFModel):
    template_id: Identifier
    page_numbering: Literal["one_based"] = "one_based"
    coordinate_system: Literal["pdf_bottom_left"] = "pdf_bottom_left"
    page_space: Literal["unrotated_crop_box"] = "unrotated_crop_box"
    fields: list[PDFField]

    @model_validator(mode="after")
    def unique_ids(self) -> PDFFieldMap:
        ids = [field.field_id for field in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("PDF field IDs must be unique")
        return self

    def lookup(self, field_id: str) -> PDFField:
        matches = [field for field in self.fields if field.field_id == field_id]
        if len(matches) != 1:
            raise KeyError(f"expected exactly one PDF field mapping for {field_id!r}")
        return matches[0]


class PDFWriteResult(PDFModel):
    artifact_created: bool = True
    output_uri: Identifier
    page_count: int = Field(ge=1, strict=True)
    written_field_ids: list[Identifier]
    warnings: list[Identifier] = Field(default_factory=list)

    @field_validator("output_uri")
    @classmethod
    def valid_uri(cls, value: str) -> str:
        document_identity(value)
        return value

    @field_validator("written_field_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Written field IDs must be unique")
        return value


class PDFProblem(PDFModel):
    code: PDFErrorCode
    message: Literal["PDF output failed; review findings remain available."] = (
        "PDF output failed; review findings remain available."
    )
