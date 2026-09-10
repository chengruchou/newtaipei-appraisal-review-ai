"""Versioned template, approved-font and field-map registry. No file I/O on import.

A registry entry binds one reviewed template version to its exact byte digest,
its complete approved field map, the declared unrotated-CropBox geometry of
every page and the redistributable fonts approved for it. Registration-time
validation catches wrong maps and pages before any PDF is opened; byte-level
template and font binding remains enforced by preflight at write time.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.adapters.local.pdf_config import (
    FontName,
    NonEmptyText,
    PDFAdapterConfig,
    PDFRenderConfig,
    PDFTemplatePolicy,
    SHA256Hex,
    field_map_sha256,
)
from appraisal_review.domain.pdf_models import PDFFieldMap

ContextKey = tuple[str, str, str]


class ApprovedFont(PDFAdapterConfig):
    """One redistributable font approved for a template version, pinned by bytes."""

    font_name: FontName
    sha256: SHA256Hex


class TemplatePageGeometry(PDFAdapterConfig):
    """Declared unrotated-CropBox page dimensions in PDF points."""

    page: int = Field(ge=1, strict=True)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = 0


class TemplateVersion(PDFAdapterConfig):
    """One approved (template, version) pair with its complete write policy."""

    template_id: NonEmptyText
    version: NonEmptyText
    policy: PDFTemplatePolicy
    field_map: PDFFieldMap
    fonts: tuple[ApprovedFont, ...] = Field(min_length=1)
    pages: tuple[TemplatePageGeometry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_registration(self) -> TemplateVersion:
        if self.policy.template_id != self.template_id:
            raise ValueError("Template policy identity must match the registry entry")
        if self.field_map.template_id != self.template_id:
            raise ValueError("Field map identity must match the registry entry")
        if not self.field_map.fields:
            raise ValueError("A registered field map must place at least one field")
        if field_map_sha256(self.field_map) != self.policy.field_map_sha256:
            raise ValueError("Registered field map differs from the approved digest")
        names = [font.font_name for font in self.fonts]
        if len(names) != len(set(names)):
            raise ValueError("Approved font names must be unique")
        declared = {geometry.page for geometry in self.pages}
        if len(declared) != len(self.pages):
            raise ValueError("Each template page is declared exactly once")
        classified = self.policy.editable_pages | self.policy.reference_only_pages
        if declared != classified:
            raise ValueError("Declared geometry must cover every classified page exactly")
        geometry_by_page = {geometry.page: geometry for geometry in self.pages}
        for field in self.field_map.fields:
            if field.page not in self.policy.editable_pages:
                raise ValueError("Registered fields must target explicitly editable pages")
            geometry = geometry_by_page[field.page]
            _x1, _y1, x2, y2 = field.bounding_box
            if x2 > geometry.width or y2 > geometry.height:
                raise ValueError("Registered field exceeds its declared page geometry")
        return self

    def contexts(self) -> tuple[ContextKey, ...]:
        """Declared comparison contexts in first-appearance field-map order."""
        ordered: dict[ContextKey, None] = {}
        for field in self.field_map.fields:
            if field.value_ref is not None:
                ref = field.value_ref
                ordered.setdefault((ref.scope, ref.target_id, ref.comparable_id), None)
        return tuple(ordered)

    def placeholder_tokens(self) -> tuple[str, ...]:
        """Opaque tokens this template writes; cloud output contains only these."""
        ordered: dict[str, None] = {}
        for field in self.field_map.fields:
            if field.placeholder_token is not None:
                ordered.setdefault(field.placeholder_token, None)
        return tuple(ordered)

    def render_configuration(
        self, base: PDFRenderConfig, *, font_name: str, font_path: Path
    ) -> PDFRenderConfig:
        """Bind a render configuration to one approved font; preflight verifies bytes."""
        matches = [font for font in self.fonts if font.font_name == font_name]
        if len(matches) != 1:
            raise ValueError("Requested font is not approved for this template version")
        return base.model_copy(
            update={
                "font_name": matches[0].font_name,
                "font_path": font_path,
                "approved_font_sha256": matches[0].sha256,
            }
        )


class TemplateRegistry(PDFAdapterConfig):
    """All approved template versions; selection is exact, never fuzzy."""

    schema_version: Literal["template-registry-v1"] = "template-registry-v1"
    templates: tuple[TemplateVersion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_versions(self) -> TemplateRegistry:
        keys = [(entry.template_id, entry.version) for entry in self.templates]
        if len(keys) != len(set(keys)):
            raise ValueError("Template versions must be registered exactly once")
        return self

    def select(self, template_id: str, version: str) -> TemplateVersion:
        matches = [
            entry
            for entry in self.templates
            if entry.template_id == template_id and entry.version == version
        ]
        if len(matches) != 1:
            raise ValueError("Requested template version is not registered")
        return matches[0]


def font_sha256(path: Path) -> str:
    """Digest a candidate font file for explicit registration."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_template_registry(path: Path) -> TemplateRegistry:
    return TemplateRegistry.model_validate_json(path.read_bytes())
