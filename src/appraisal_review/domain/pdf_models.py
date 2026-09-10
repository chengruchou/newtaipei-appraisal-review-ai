"""Authoritative public PDF contract; never carries PDF bytes or base64."""

from __future__ import annotations

from pydantic import Field, model_validator

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
    protected_source_uris: list[Identifier] = Field(default_factory=list)
    result: FactorReviewResult
    additional_results: list[FactorReviewResult] = Field(default_factory=list)
    field_map: PDFFieldMap

    def comparisons(self) -> list[FactorReviewResult]:
        """All verified comparisons; the legacy single-context request has one."""
        return [self.result, *self.additional_results]

    @model_validator(mode="after")
    def validate_request(self) -> PDFWriteRequest:
        destination_identity = document_identity(self.destination_uri)
        protected_identities = {
            document_identity(uri) for uri in [self.source_uri, *self.protected_source_uris]
        }
        if destination_identity in protected_identities:
            raise SourceDestinationConflictError(
                "Destination must differ from the template and every reviewed source"
            )
        if not self.field_map.fields:
            raise PDFFieldPlacementError("A write requires at least one mapped field")
        comparisons = self.comparisons()
        if self.additional_results:
            if any(comparison.context is None for comparison in comparisons):
                raise PDFFieldPlacementError(
                    "Every comparison in a multiple-context write must bind its context"
                )
            keys = [
                (c.context.scope, c.context.target_id, c.context.comparable_id)
                for c in comparisons
                if c.context is not None
            ]
            if len(keys) != len(set(keys)):
                raise PDFFieldPlacementError("Multiple-context comparisons must be unique")
            if len({comparison.case_id for comparison in comparisons}) != 1:
                raise PDFFieldPlacementError("Multiple-context comparisons must belong to one case")
        contexts = set()
        for field in self.field_map.fields:
            ref = field.value_ref
            if ref is None:
                if field.placeholder_token is None:
                    raise PDFFieldPlacementError("A write requires an explicit value reference")
                continue
            contexts.add((ref.scope, ref.target_id, ref.comparable_id))
        if self.additional_results and any(
            comparison.context is not None
            and (
                comparison.context.scope,
                comparison.context.target_id,
                comparison.context.comparable_id,
            )
            not in contexts
            for comparison in comparisons
        ):
            raise PDFFieldPlacementError(
                "Every verified comparison must be written; a partial report is unsupported"
            )
        for field in self.field_map.fields:
            ref = field.value_ref
            if ref is None:
                continue
            target = self._bound_comparison(ref, comparisons)
            if ref.factor_id is not None:
                matches = [r for r in target.results if r.factor_id == ref.factor_id]
                if len(matches) != 1 or getattr(matches[0], ref.value) is None:
                    raise PDFFieldPlacementError("A value reference must resolve exactly once")
            elif target.summary.total_adjustment_percent is None:
                raise PDFFieldPlacementError("A total reference requires a computed total")
        if not self.additional_results and len(contexts) > 1:
            raise PDFFieldPlacementError("The current result supports one comparison context")
        return self

    def _bound_comparison(
        self, ref: PDFValueRef, comparisons: list[FactorReviewResult]
    ) -> FactorReviewResult:
        """Resolve one field reference to exactly one context-bound comparison."""
        if not self.additional_results and self.result.context is None:
            return self.result
        matching = [
            comparison
            for comparison in comparisons
            if comparison.context is not None
            and (ref.scope, ref.target_id, ref.comparable_id)
            == (
                comparison.context.scope,
                comparison.context.target_id,
                comparison.context.comparable_id,
            )
        ]
        if len(matching) != 1:
            raise PDFFieldPlacementError("Field reference does not match verified comparison")
        return matching[0]
