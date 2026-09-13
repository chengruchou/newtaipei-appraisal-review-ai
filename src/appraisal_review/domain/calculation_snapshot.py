"""The one immutable value set an official export is filled from.

A workbook writer must not read live case state, because a task answered between two
writes would put two revisions in one delivered file. It reads a snapshot: every value the
three official tables need, keyed by the same source strings the table mappings bind, with
each value's unit, absence state and origin carried alongside so the writer never guesses.

Three distinctions the models refuse to blur:

- Absent is not zero. ``missing``, ``not_applicable`` and ``confirmed_zero`` are separate
  states and only ``present`` carries a value, so a blank input can never render as 0.
- Origin is recorded, not inferred. A value copied from the assignment's own inputs is
  ``given_input``; one our engine derived is ``computed`` and carries its arithmetic trace;
  one a reviewer asserted is ``human_confirmed``. The pre-filled sample workbook's answers
  are none of these - they are acceptance references and must never enter a snapshot.
- Percentages carry their convention. A correction of five percentage points is stored
  once, as points; the table-4 fraction form (0.05) is a rendering decision the mapping
  makes, never a second stored value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    RevisionReference,
    ServiceModel,
)

ValueState = Literal["present", "missing", "not_applicable", "confirmed_zero"]
ValueOrigin = Literal["computed", "given_input", "human_confirmed", "imported_result"]
#: ``imported_result`` is a result produced outside this service and read in whole:
#: traceable to a pinned commit, file digest, sheet and cell, but asserting no human
#: decision. It stays subject to the formal approval gate exactly like any draft, and
#: must never be written where ``human_confirmed`` is meant.

#: Subject identifiers for this round's single real case: one comparison-base parcel and
#: three comparables. Four subjects, one case - never four cases.
SubjectRole = Literal["comparison_base", "comparable"]


class SnapshotSubject(ServiceModel):
    subject_id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    role: SubjectRole
    label: str = Field(min_length=1, max_length=128)


class SnapshotEntry(ServiceModel):
    """One value a mapping's CellBinding.source can address."""

    state: ValueState
    # str first, resolved left to right: JSON cannot say "this string is a date, that one
    # is a number", and smart-union picks Decimal for numeric-looking strings only when
    # loading from JSON - so the same file parsed two ways produced two types. Every
    # consumer that needs arithmetic coerces through Decimal(str) anyway; a date string
    # must survive as exactly the text the source stated.
    value: str | Decimal | None = Field(default=None, union_mode="left_to_right")
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    origin: ValueOrigin | None = None
    trace: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def state_value_origin(self) -> SnapshotEntry:
        if (self.state == "present") != (self.value is not None):
            raise ValueError("Only a present entry carries a value")
        if self.state == "present" and self.origin is None:
            raise ValueError("A present value must state where it came from")
        if self.origin == "computed" and not self.trace.strip():
            raise ValueError("A computed value must carry its arithmetic trace")
        return self


class CalculationSnapshot(ServiceModel):
    """Everything the official tables read, frozen against one case revision."""

    schema_version_snapshot: Literal["calculation-snapshot-v1"] = "calculation-snapshot-v1"
    revision: RevisionReference
    district: str = Field(min_length=1, max_length=64)
    valuation_date: date
    rule_bundle_id: str = Field(min_length=1, max_length=128)
    rule_bundle_version: str = Field(min_length=1, max_length=128)
    subjects: tuple[SnapshotSubject, ...] = Field(min_length=1, max_length=16)
    entries: dict[str, SnapshotEntry] = Field(min_length=1)
    #: Source keys the tables need but this snapshot could not provide, each with a reason.
    #: A writer renders these per the mapping's absence policy and the export lists them
    #: as blockers; it never invents a value to make a sheet look finished.
    gaps: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent(self) -> CalculationSnapshot:
        if len({s.subject_id for s in self.subjects}) != len(self.subjects):
            raise ValueError("Subject identifiers must be unique")
        if sum(1 for s in self.subjects if s.role == "comparison_base") != 1:
            raise ValueError("Exactly one comparison-base subject per case")
        overlap = set(self.gaps) & set(self.entries)
        if overlap:
            raise ValueError(f"A key is either provided or a gap, not both: {sorted(overlap)}")
        return self

    def digest(self) -> Digest:
        return content_digest(self)
