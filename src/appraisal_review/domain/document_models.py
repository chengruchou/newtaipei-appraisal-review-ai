"""Versioned source registry. Coordinates are unrotated CropBox bottom-left points."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Box = tuple[float, float, float, float]


class DocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class SourceRegion(DocumentModel):
    id: str
    kind: Literal["text", "cell", "selection", "image"]
    bbox: Box
    text: str = ""
    table_id: str | None = None
    row: int | None = None
    column: int | None = None
    selection: Literal["checked", "unchecked", "ambiguous"] | None = None


class SourcePage(DocumentModel):
    number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = 0
    crop_box: Box
    has_text: bool
    regions: list[SourceRegion]

    @model_validator(mode="after")
    def valid_regions(self) -> SourcePage:
        ids = [region.id for region in self.regions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source region")
        for region in self.regions:
            x0, y0, x1, y1 = region.bbox
            if not (0 <= x0 < x1 <= self.width and 0 <= y0 < y1 <= self.height):
                raise ValueError("source region outside page")
        return self


class SourceDocument(DocumentModel):
    document_id: str
    uri: str
    content_hash: Digest
    version: str = Field(min_length=1)
    role: Literal["criteria", "forms", "reference", "brief"]
    document_date: str | None = None
    coordinate_system: Literal["pdf_bottom_left"] = "pdf_bottom_left"
    page_space: Literal["unrotated_crop_box"] = "unrotated_crop_box"
    pages: list[SourcePage] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_pages(self) -> SourceDocument:
        if [p.number for p in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("source pages must be complete and ordered")
        return self


class SourceCitation(DocumentModel):
    document_id: str
    content_hash: Digest
    version: str
    page: int = Field(ge=1)
    region_id: str
    bbox: Box
    excerpt: str


class SourceRegistry(DocumentModel):
    documents: list[SourceDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_documents(self) -> SourceRegistry:
        ids = [d.document_id for d in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source document")
        return self

    def resolves(self, citation: SourceCitation) -> bool:
        for document in self.documents:
            if (document.document_id, document.content_hash, document.version) != (
                citation.document_id,
                citation.content_hash,
                citation.version,
            ):
                continue
            if citation.page > len(document.pages):
                return False
            for region in document.pages[citation.page - 1].regions:
                if region.id == citation.region_id:
                    # Image localization proves position, never semantic correctness.
                    return region.bbox == citation.bbox and (
                        (
                            region.kind in {"image", "cell", "selection"}
                            and not region.text
                            and not citation.excerpt
                        )
                        or (bool(citation.excerpt) and citation.excerpt in region.text)
                    )
        return False
