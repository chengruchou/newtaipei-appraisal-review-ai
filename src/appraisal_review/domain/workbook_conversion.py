"""Verification contracts for converting a delivered workbook to PDF.

The official tables are delivered as a filled `.xlsx` copy; the PDF handed to a
reviewer must be that same copy, converted, and nothing else. Nothing in a PDF
proves which workbook produced it, so provenance here is an operator assertion
that this module records and checks for consistency, never a proof. The checks
answer the questions a reviewer cannot answer by eye on someone else's machine:
did every page render, is every font embedded, did required content survive the
print area, and did a hidden legacy example leak into the output.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.service_contracts import ContractModel


class ConversionExpectation(ContractModel):
    """What the delivered PDF must show, declared before conversion runs."""

    schema_version: Literal["workbook-conversion-v1"] = "workbook-conversion-v1"
    source_name: str = Field(min_length=1, max_length=512)
    source_digest: Digest
    expected_page_count: int | None = Field(default=None, ge=1)
    required_text: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()

    @model_validator(mode="after")
    def distinct_probes(self) -> ConversionExpectation:
        overlap = set(self.required_text) & set(self.forbidden_text)
        if overlap:
            raise ValueError(f"Text cannot be both required and forbidden: {sorted(overlap)}")
        if any(not probe.strip() for probe in self.required_text + self.forbidden_text):
            raise ValueError("Text probes must carry content")
        return self


class PageObservation(ContractModel):
    """One rendered page as observed in the output file."""

    number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = 0
    character_count: int = Field(ge=0)


class FontObservation(ContractModel):
    """One font resource. A non-embedded font renders differently elsewhere."""

    name: str = Field(min_length=1, max_length=256)
    embedded: bool
    subset: bool = False
    pages: tuple[int, ...] = Field(min_length=1)


class ConversionRecord(ContractModel):
    """An operator-asserted conversion and the observations made of its output.

    `source_digest` states which workbook bytes were converted. This record does
    not and cannot verify that claim from the PDF; an independent check means
    re-running the conversion from the digest-pinned source.
    """

    schema_version: Literal["workbook-conversion-v1"] = "workbook-conversion-v1"
    source_digest: Digest
    converter: str = Field(min_length=1, max_length=256)
    output_name: str = Field(min_length=1, max_length=512)
    output_digest: Digest
    output_byte_size: int = Field(ge=0)
    encrypted: bool = False
    pages: tuple[PageObservation, ...] = ()
    fonts: tuple[FontObservation, ...] = ()
    found_text: tuple[str, ...] = ()

    @model_validator(mode="after")
    def ordered_pages(self) -> ConversionRecord:
        if [page.number for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("Page numbers must be contiguous and start at one")
        highest = len(self.pages)
        for font in self.fonts:
            if any(page < 1 or page > highest for page in font.pages):
                raise ValueError(f"Font {font.name} references a page outside the output")
        return self


class ConversionFinding(ContractModel):
    """One reviewable statement about a conversion. Never an approval."""

    code: Literal[
        "source_digest_mismatch",
        "encrypted_output",
        "no_pages",
        "page_count_mismatch",
        "blank_page",
        "font_not_embedded",
        "missing_required_text",
        "forbidden_text_present",
    ]
    detail: str = Field(max_length=512)


def verify_conversion(
    expectation: ConversionExpectation, record: ConversionRecord
) -> tuple[ConversionFinding, ...]:
    """Report every inconsistency between what was expected and what was observed."""

    findings: list[ConversionFinding] = []
    if record.source_digest != expectation.source_digest:
        findings.append(
            ConversionFinding(
                code="source_digest_mismatch",
                detail=(
                    f"The record converted {record.source_digest}, but the delivered "
                    f"{expectation.source_name} is {expectation.source_digest}"
                ),
            )
        )
    if record.encrypted:
        findings.append(
            ConversionFinding(
                code="encrypted_output", detail="The output is encrypted and cannot be checked"
            )
        )
    if not record.pages:
        findings.append(ConversionFinding(code="no_pages", detail="The output has no pages"))
    elif (
        expectation.expected_page_count is not None
        and len(record.pages) != expectation.expected_page_count
    ):
        findings.append(
            ConversionFinding(
                code="page_count_mismatch",
                detail=(
                    f"Expected {expectation.expected_page_count} pages, "
                    f"observed {len(record.pages)}"
                ),
            )
        )
    for page in record.pages:
        if page.character_count == 0:
            findings.append(
                ConversionFinding(
                    code="blank_page",
                    detail=f"Page {page.number} carries no extractable text",
                )
            )
    for font in record.fonts:
        if not font.embedded:
            pages = ", ".join(str(page) for page in font.pages)
            findings.append(
                ConversionFinding(
                    code="font_not_embedded",
                    detail=(
                        f"{font.name} is not embedded and will render with a substitute "
                        f"on another machine; pages {pages}"
                    ),
                )
            )
    found = set(record.found_text)
    for probe in expectation.required_text:
        if probe not in found:
            findings.append(
                ConversionFinding(
                    code="missing_required_text",
                    detail=f"Required content is absent from the output: {probe[:200]!r}",
                )
            )
    for probe in expectation.forbidden_text:
        if probe in found:
            findings.append(
                ConversionFinding(
                    code="forbidden_text_present",
                    detail=f"Content that must not appear is in the output: {probe[:200]!r}",
                )
            )
    return tuple(findings)
