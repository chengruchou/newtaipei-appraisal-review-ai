"""Authoritative public PDF contract; never carries PDF bytes or base64."""

from __future__ import annotations

from pydantic import model_validator

from appraisal_review.domain.factor_models import FactorReviewResult
from appraisal_review.domain.pdf_types import (
    Identifier,
    InvalidPDFResultError,
    PDFErrorCode,
    PDFField,
    PDFFieldMap,
    PDFFieldPlacementError,
    PDFFontError,
    PDFModel,
    PDFProblem,
    PDFReadError,
    PDFValueRef,
    PDFWriteError,
    PDFWriteResult,
    SourceDestinationConflictError,
    UnsupportedDocumentURIError,
    document_identity,
)

__all__ = [
    "InvalidPDFResultError",
    "PDFErrorCode",
    "PDFField",
    "PDFFieldMap",
    "PDFFieldPlacementError",
    "PDFFontError",
    "PDFProblem",
    "PDFReadError",
    "PDFValueRef",
    "PDFWriteError",
    "PDFWriteRequest",
    "PDFWriteResult",
    "SourceDestinationConflictError",
    "UnsupportedDocumentURIError",
    "document_identity",
]


class PDFWriteRequest(PDFModel):
    source_uri: Identifier
    destination_uri: Identifier
    result: FactorReviewResult
    field_map: PDFFieldMap

    @model_validator(mode="after")
    def validate_request(self) -> PDFWriteRequest:
        if document_identity(self.source_uri) == document_identity(self.destination_uri):
            raise SourceDestinationConflictError("Source and destination must differ")
        if not self.field_map.fields:
            raise PDFFieldPlacementError("A write requires at least one mapped field")
        contexts = set()
        for field in self.field_map.fields:
            ref = field.value_ref
            if ref is None:
                raise PDFFieldPlacementError("A write requires an explicit value reference")
            contexts.add((ref.scope, ref.target_id, ref.comparable_id))
            if ref.factor_id is not None:
                matches = [r for r in self.result.results if r.factor_id == ref.factor_id]
                if len(matches) != 1 or getattr(matches[0], ref.value) is None:
                    raise PDFFieldPlacementError("A value reference must resolve exactly once")
            elif self.result.summary.total_adjustment_percent is None:
                raise PDFFieldPlacementError("A total reference requires a computed total")
        if len(contexts) != 1:
            raise PDFFieldPlacementError("The current result supports one comparison context")
        return self
