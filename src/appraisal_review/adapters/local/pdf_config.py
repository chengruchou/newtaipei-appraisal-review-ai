"""Validated, provider-local PDF rendering configuration with no file I/O."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from appraisal_review.config import Settings
from appraisal_review.domain.factor_models import Grade
from appraisal_review.domain.pdf_models import PDFFieldPlacementError

NonEmptyText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
FontName = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
    ),
]
RGBColor = Annotated[
    str,
    StringConstraints(strict=True, to_upper=True, pattern=r"^#[0-9A-Fa-f]{6}$"),
]


class PDFAdapterConfig(BaseModel):
    """Base configuration that rejects coercion and unknown settings."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PDFRenderConfig(PDFAdapterConfig):
    """Deterministic text and publication policy injected into a local writer.

    Construction validates values lexically but never checks whether the font
    exists. The writer performs that I/O check when handling a write request.
    """

    font_path: Path
    font_name: FontName = "AppraisalCJK"
    font_size: float = Field(default=10.0, gt=0.0, le=72.0)
    text_color: RGBColor = "#000000"
    text_alignment: Literal["left", "center", "right"] = "center"
    annotation_label: NonEmptyText = "REVIEW"
    annotation_color: RGBColor = "#B00020"
    decimal_places: int = Field(default=2, ge=0, le=6)
    show_positive_sign: bool = True
    overwrite_existing: bool = False
    grade_labels: dict[str, NonEmptyText] = Field(
        default_factory=lambda: {grade.value: grade.value for grade in Grade}
    )

    @model_validator(mode="after")
    def require_absolute_font_path(self) -> PDFRenderConfig:
        if not self.font_path.is_absolute():
            raise ValueError("PDF font path must be absolute")
        if set(self.grade_labels) != {grade.value for grade in Grade}:
            raise ValueError("PDF grade labels must cover every supported grade exactly")
        return self


class PDFTemplatePolicy(PDFAdapterConfig):
    """Version-specific editable and reference-only page boundaries."""

    template_id: NonEmptyText
    editable_pages: frozenset[int]
    reference_only_pages: frozenset[int] = frozenset()

    @model_validator(mode="after")
    def validate_page_sets(self) -> PDFTemplatePolicy:
        all_pages = self.editable_pages | self.reference_only_pages
        if not self.editable_pages:
            raise ValueError("A PDF template must declare at least one editable page")
        if any(type(page) is not int or page < 1 for page in all_pages):
            raise ValueError("PDF template pages must be positive integers")
        if self.editable_pages & self.reference_only_pages:
            raise ValueError("Editable and reference-only pages must not overlap")
        return self

    def require_editable_page(self, page: int) -> None:
        if page not in self.editable_pages:
            raise PDFFieldPlacementError("PDF field targets a page that is not explicitly editable")


def render_config_from_settings(settings: Settings) -> PDFRenderConfig:
    """Build strict adapter configuration only when a real writer is composed."""
    return PDFRenderConfig(
        font_path=Path(settings.pdf_font_path),
        font_name=settings.pdf_font_name,
        font_size=settings.pdf_font_size,
        text_color=settings.pdf_text_color,
        text_alignment=settings.pdf_text_alignment,
        annotation_label=settings.pdf_annotation_label,
        annotation_color=settings.pdf_annotation_color,
        decimal_places=settings.pdf_decimal_places,
        show_positive_sign=settings.pdf_show_positive_sign,
        overwrite_existing=settings.pdf_overwrite_existing,
        grade_labels=settings.pdf_grade_labels,
    )
