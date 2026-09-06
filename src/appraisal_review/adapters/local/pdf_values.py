"""Deterministic mapping from typed review results to PDF display text."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from appraisal_review.adapters.local.pdf_config import PDFRenderConfig
from appraisal_review.domain.factor_models import FactorReviewResult, Grade
from appraisal_review.domain.pdf_models import PDFFieldPlacementError, PDFValueRef


class PDFValueFormatter:
    """Resolve one explicit value reference and format it without inference."""

    def __init__(self, config: PDFRenderConfig) -> None:
        self.config = config

    def resolve(self, result: FactorReviewResult, reference: PDFValueRef) -> str:
        if reference.value == "total_adjustment_percent":
            value = result.summary.total_adjustment_percent
            if value is None:
                raise PDFFieldPlacementError("PDF total reference has no computed value")
            return self._format_percent(value)

        if reference.factor_id is None:
            raise PDFFieldPlacementError("PDF factor reference requires a factor ID")
        matches = [item for item in result.results if item.factor_id == reference.factor_id]
        if len(matches) != 1:
            raise PDFFieldPlacementError("PDF factor reference must resolve exactly once")
        value = getattr(matches[0], reference.value)
        if value is None:
            raise PDFFieldPlacementError("PDF factor reference has no computed value")
        if reference.value in {"target_grade", "comparable_grade"}:
            if not isinstance(value, Grade):
                raise PDFFieldPlacementError("PDF grade reference did not resolve to a grade")
            try:
                return self.config.grade_labels[value.value]
            except KeyError as error:
                raise PDFFieldPlacementError("PDF grade has no configured display label") from error
        if reference.value == "adjustment_percent" and isinstance(value, int | float):
            return self._format_percent(float(value))
        raise PDFFieldPlacementError("PDF value reference resolved to an unsupported value")

    def _format_percent(self, value: float) -> str:
        if isinstance(value, bool) or not math.isfinite(value):
            raise PDFFieldPlacementError("PDF percentage must be finite")
        try:
            decimal_value = Decimal(str(value))
            quantum = Decimal(1).scaleb(-self.config.decimal_places)
            rounded = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
        except InvalidOperation as error:
            raise PDFFieldPlacementError("PDF percentage cannot be formatted") from error
        if rounded == 0:
            rounded = abs(rounded)
        rendered = f"{rounded:.{self.config.decimal_places}f}"
        if self.config.show_positive_sign and rounded > 0:
            rendered = f"+{rendered}"
        return f"{rendered}%"
