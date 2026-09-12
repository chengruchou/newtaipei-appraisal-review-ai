"""Convert a filled official workbook to PDF. The workbook stays the only source.

The delivery flow renders the PDF from the same workbook the reviewer approved, so
the converter is a faithful renderer and never a second author. It may not change
values, refresh an external link, recalculate formulas, or redraw the sheet from
extracted data. A converter that cannot guarantee that is reported as unavailable
rather than substituted.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

Capability = Literal["ready", "converter_missing", "fonts_missing", "unverified"]


class ConversionUnavailable(Exception):
    """No faithful conversion is possible. Carries a code, never a path or content."""


@dataclass(frozen=True)
class ConversionCapability:
    """What the host can actually do, reported without attempting a conversion."""

    state: Capability
    converter: str | None
    converter_version: str | None
    missing_scripts: tuple[str, ...]
    detail: str

    @property
    def ready(self) -> bool:
        return self.state == "ready"


@dataclass(frozen=True)
class ConvertedWorkbook:
    """A PDF rendered from exact workbook bytes, pinned to those bytes."""

    content: bytes
    page_count: int
    workbook_sha256: str
    output_sha256: str
    converter: str
    converter_version: str


class WorkbookConverter(Protocol):
    def capability(self) -> ConversionCapability:
        """Report readiness without converting. Never claims a missing converter works."""
        ...

    def convert(self, workbook: bytes, *, expected_sha256: str) -> ConvertedWorkbook:
        """Render the exact given workbook bytes. A digest mismatch fails closed."""
        ...
