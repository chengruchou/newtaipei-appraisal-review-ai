"""Declared organizer inputs for one case. A manifest describes, it never supplies.

The review pipeline consumes a `SourceRegistry` whose documents already carry exact
page geometry, regions and digests, and that registry can only be built by parsing
the real files. Before those files exist locally there is still work that must be
pinned down and reviewed: which documents the case needs, what each one is used
for, which subject each label denotes, which date applies, and which questions the
organizer or reviewer must answer before any rule may be selected.

That is what a manifest holds. It deliberately cannot become evidence:

- `content_sha256` is optional and stays `None` until a human supplies the real
  file and reviews its digest. A missing digest is reported as missing, never
  treated as satisfied and never filled in from an observed file automatically.
- Declared structure (page or worksheet counts) is an expectation to check a
  supplied file against, not a measurement of one.
- Nothing here proposes a rule, a factor grade, a weight or a value. Open
  questions record what is unresolved so the absence stays visible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.extraction_contracts import ContractModel, PositiveCount

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Text = Annotated[str, Field(min_length=1, max_length=512)]
ISODate = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
Usage = Literal["case_source", "rule_source", "output_template", "reference"]
RegistryRole = Literal["criteria", "forms", "reference", "brief"]
# How this exact declared file may be processed. The scope belongs to the reviewed
# source configuration, so it is recorded once per file and is never a runtime
# question put to an operator. `privacy_required` stays the default: a file reaches
# the direct path only because a named reviewer decision says this one may.
Handling = Literal["privacy_required", "direct_non_sensitive"]
# A registry source must declare the role the parsed SourceDocument will carry;
# an output template is consumed by the template registry and is not a registry source.
_REGISTRY_USAGE = {"case_source", "rule_source", "reference"}


class SubjectExpectation(ContractModel):
    """One labelled property in the case and the role the organizer assigned it.

    `brief_page` records where the label appears, and is never how the role is
    decided: the brief presents the comparables before the subject.
    """

    label: Text
    role: Literal["subject", "comparable"]
    brief_page: PositiveCount | None = None
    note: str = Field(default="", max_length=1024)


class CaseExpectation(ContractModel):
    case_label: Text
    district: Text
    land_use_description: Text
    effective_date: ISODate
    effective_date_as_written: Text
    subjects: tuple[SubjectExpectation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def one_subject_with_unique_labels(self) -> CaseExpectation:
        labels = [subject.label for subject in self.subjects]
        if len(set(labels)) != len(labels):
            raise ValueError("Duplicate subject label")
        if sum(subject.role == "subject" for subject in self.subjects) != 1:
            raise ValueError("Exactly one subject property is expected")
        if not any(subject.role == "comparable" for subject in self.subjects):
            raise ValueError("At least one comparable is expected")
        return self

    def role_of(self, label: str) -> str:
        matches = [subject for subject in self.subjects if subject.label == label]
        if len(matches) != 1:
            raise ValueError("Unknown subject label")
        return matches[0].role


class SourceExpectation(ContractModel):
    """One expected organizer file, its declared use and its optional pinned digest."""

    source_id: Identifier
    title: Text
    medium: Literal["pdf", "workbook"]
    usage: Usage
    registry_role: RegistryRole | None = None
    purposes: tuple[Literal["case", "rule", "procedure"], ...] = ()
    handling: Handling = "privacy_required"
    handling_reference: Text | None = None
    declared_version: Text | None = None
    content_sha256: Digest | None = None
    byte_size: Annotated[int, Field(gt=0, strict=True)] | None = None
    page_count: PositiveCount | None = None
    sheet_count: PositiveCount | None = None
    hidden_sheet_count: Annotated[int, Field(ge=0, strict=True)] | None = None
    visible_worksheet: Text | None = None
    native_text_expected: bool = True
    notes: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def coherent_expectation(self) -> SourceExpectation:
        if (self.usage in _REGISTRY_USAGE) != (self.registry_role is not None):
            raise ValueError("A registry source declares exactly one registry role")
        if self.handling == "direct_non_sensitive" and not self.handling_reference:
            # Skipping the privacy route requires a recorded decision, not a default.
            raise ValueError("Direct handling requires a recorded reviewer decision")
        if self.medium == "pdf" and (
            self.sheet_count or self.hidden_sheet_count is not None or self.visible_worksheet
        ):
            raise ValueError("Worksheet structure does not apply to a PDF")
        if self.medium == "workbook" and self.page_count:
            raise ValueError("Page structure does not apply to a workbook")
        if (
            self.sheet_count is not None
            and self.hidden_sheet_count is not None
            and self.hidden_sheet_count >= self.sheet_count
        ):
            raise ValueError("A workbook needs at least one visible worksheet")
        if self.usage == "rule_source" and self.registry_role != "criteria":
            raise ValueError("Rule evidence requires the criteria role")
        if self.usage == "case_source" and self.registry_role != "forms":
            raise ValueError("Case evidence requires the forms role")
        return self

    @property
    def pinned(self) -> bool:
        return self.content_sha256 is not None

    def document_version(self) -> str:
        """The version string a parsed SourceDocument carries for this file.

        When the organizer file states no edition, the pinned content digest is the
        version: it identifies exactly these bytes and nothing else. Inventing an
        edition number would be worse than naming the digest.
        """
        if self.declared_version:
            return self.declared_version
        if self.content_sha256 is None:
            raise ValueError("An unpinned source without a declared version has no version")
        return f"sha256:{self.content_sha256[:16]}"


class OpenQuestion(ContractModel):
    """An unresolved input. Recording it keeps the gap visible; it resolves nothing."""

    question_id: Identifier
    topic: Text
    question: Annotated[str, Field(min_length=1, max_length=2048)]
    owner: Literal["organizer", "reviewer", "operator"]
    blocks: Literal["rule_selection", "calculation", "output", "none"]
    source_id: Identifier | None = None


class CaseSourceManifest(ContractModel):
    schema_version: Literal["case-source-manifest-v1"] = "case-source-manifest-v1"
    manifest_id: Identifier
    case: CaseExpectation
    sources: tuple[SourceExpectation, ...] = Field(min_length=1)
    open_questions: tuple[OpenQuestion, ...] = ()

    @model_validator(mode="after")
    def unambiguous_sources(self) -> CaseSourceManifest:
        ids = [source.source_id for source in self.sources]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate source expectation")
        if len({q.question_id for q in self.open_questions}) != len(self.open_questions):
            raise ValueError("Duplicate open question")
        if any(q.source_id is not None and q.source_id not in ids for q in self.open_questions):
            raise ValueError("Open question references an undeclared source")
        digests = [s.content_sha256 for s in self.sources if s.content_sha256 is not None]
        if len(set(digests)) != len(digests):
            raise ValueError("Two sources cannot pin the same content")
        for role in ("criteria", "forms"):
            if sum(source.registry_role == role for source in self.sources) != 1:
                # SourcePurposes.selected requires exactly one current document per
                # selected role, so a case with two criteria or two forms sources
                # cannot be assembled without an explicit selection decision.
                raise ValueError(f"Exactly one {role} source is expected")
        return self

    @property
    def digest(self) -> str:
        value = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()

    def source(self, source_id: str) -> SourceExpectation:
        matches = [source for source in self.sources if source.source_id == source_id]
        if len(matches) != 1:
            raise ValueError("Unknown source expectation")
        return matches[0]

    def unpinned(self) -> tuple[str, ...]:
        """Sources whose real bytes no reviewer has pinned yet."""
        return tuple(source.source_id for source in self.sources if not source.pinned)

    def registry_sources(self) -> tuple[SourceExpectation, ...]:
        """Sources that become SourceRegistry documents. Templates are excluded."""
        return tuple(source for source in self.sources if source.registry_role is not None)

    def direct_sources(self) -> tuple[SourceExpectation, ...]:
        """Registry sources a reviewer recorded as processable without the privacy route."""
        return tuple(
            source
            for source in self.registry_sources()
            if source.handling == "direct_non_sensitive" and source.pinned
        )

    def blocking_questions(self) -> tuple[OpenQuestion, ...]:
        return tuple(q for q in self.open_questions if q.blocks != "none")


def compare(
    expected: SourceExpectation,
    *,
    content_sha256: str,
    byte_size: int,
    structure: Mapping[str, int | str],
) -> tuple[str, tuple[str, ...]]:
    """Compare one supplied file against its expectation. Never rewrites the manifest.

    Returns a state and the exact differences. An unpinned expectation reports
    `unpinned` rather than `satisfied`, because an observed digest is a measurement
    and only a reviewer can turn it into a pin.
    """
    differences: list[str] = []
    if expected.byte_size is not None and expected.byte_size != byte_size:
        differences.append("byte_size")
    for field, observed in structure.items():
        declared = getattr(expected, field, None)
        if declared is not None and declared != observed:
            differences.append(field)
    if expected.content_sha256 is not None and expected.content_sha256 != content_sha256:
        differences.append("content_sha256")
        return "changed", tuple(differences)
    if differences:
        return "structure_differs", tuple(differences)
    return ("satisfied" if expected.pinned else "unpinned"), ()
