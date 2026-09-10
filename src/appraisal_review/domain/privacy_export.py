"""Export carriers and value-free regression reports; carriers are not grants."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field, model_validator

from appraisal_review.domain.privacy_models import PrivacyModel


@dataclass(frozen=True)
class LocalTextDraft:
    text: str = field(repr=False)
    state: Literal["needs_review"] = "needs_review"


@dataclass(frozen=True)
class PrivacyExportPayload:
    pdf: bytes = field(repr=False)
    manifest_json: str = field(repr=False)
    reviewer_text: str | None = field(default=None, repr=False)
    filename: Literal["sanitized.pdf"] = field(default="sanitized.pdf", init=False)


LeakSurface = Literal[
    "bundle_bytes",
    "pdf_streams",
    "pdf_text",
    "pdf_images",
    "stdout",
    "stderr",
    "log",
    "exception",
    "serializer",
    "http",
    "sdk",
    "telemetry",
]
CanaryId = Literal["c01", "c02", "c03"]


class CanaryHit(PrivacyModel):
    canary_id: CanaryId
    count: int = Field(gt=0)


class LeakCheck(PrivacyModel):
    surface: LeakSurface
    inspected: bool
    hits: tuple[CanaryHit, ...] = ()

    @model_validator(mode="after")
    def unique_hits(self) -> "LeakCheck":
        if len({h.canary_id for h in self.hits}) != len(self.hits):
            raise ValueError("Duplicate canary ID")
        if self.hits and not self.inspected:
            raise ValueError("Uninspected surface has hits")
        return self


class LeakScanReport(PrivacyModel):
    schema_version: Literal["privacy-leak-report-v1"] = "privacy-leak-report-v1"
    scope: Literal["python_hooks", "linux_network_namespace"]
    checks: tuple[LeakCheck, ...]
    status: Literal["passed", "failed", "blocked"]

    @model_validator(mode="after")
    def consistent_status(self) -> "LeakScanReport":
        from typing import get_args

        if len({c.surface for c in self.checks}) != len(self.checks):
            raise ValueError("Duplicate surface")
        complete = set(c.surface for c in self.checks) == set(get_args(LeakSurface))
        expected = (
            "failed"
            if any(c.hits for c in self.checks)
            else "passed"
            if complete and all(c.inspected for c in self.checks)
            else "blocked"
        )
        if self.status != expected:
            raise ValueError("Report status differs from evidence")
        return self
