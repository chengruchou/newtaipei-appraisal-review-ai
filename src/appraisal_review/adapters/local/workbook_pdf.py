"""Render a filled workbook to PDF with a local headless office converter.

The converter is the only component that turns an approved workbook into the
delivered PDF, so it renders the exact bytes it is given and verifies the result:
correct digest in, a real PDF out, every page present, text actually extractable,
and any required strings present. A host without a converter, or without fonts for
the document's script, is reported as unavailable. Nothing here re-authors a sheet
from extracted values, refreshes an external link or recalculates a formula.

The subprocess runs headless in a private user profile directory so it cannot join
an interactive office session, and it is bounded by a timeout.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from appraisal_review.ports.workbook_conversion import (
    ConversionCapability,
    ConversionUnavailable,
    ConvertedWorkbook,
)

# Candidate names for the same converter. `soffice` is the stable entry point.
CONVERTER_NAMES = ("soffice", "libreoffice")
# Scripts the official forms need. Han covers the Traditional Chinese headings.
REQUIRED_SCRIPTS = ("Han",)
_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class ConverterLimits:
    timeout_seconds: float = 180.0
    max_workbook_bytes: int = 32 * 1024 * 1024
    max_output_bytes: int = 64 * 1024 * 1024
    max_pages: int = 200


class LocalWorkbookConverter:
    """Headless office conversion of exact workbook bytes."""

    def __init__(
        self,
        *,
        limits: ConverterLimits | None = None,
        executable: Path | None = None,
        runner: object = None,
    ) -> None:
        self.limits = limits or ConverterLimits()
        self._executable = executable
        # Injected for tests. Production uses subprocess.run through _run.
        self._runner = runner

    def _locate(self) -> Path | None:
        if self._executable is not None:
            return self._executable if self._executable.is_file() else None
        for name in CONVERTER_NAMES:
            found = shutil.which(name)
            if found:
                return Path(found)
        return None

    def _run(self, command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
        if self._runner is not None:
            assert callable(self._runner)
            result = self._runner(command, cwd)
            assert isinstance(result, subprocess.CompletedProcess)
            return result
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            timeout=self.limits.timeout_seconds,
            check=False,
        )

    def _version(self, executable: Path) -> str | None:
        try:
            with tempfile.TemporaryDirectory() as work:
                result = self._run([str(executable), "--version"], cwd=Path(work))
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        match = _VERSION.search(result.stdout.decode("utf-8", "replace"))
        return match.group(1) if match else None

    @staticmethod
    def _missing_scripts() -> tuple[str, ...] | None:
        """Ask fontconfig which required scripts have no font. None means unknown."""
        if shutil.which("fc-list") is None:
            return None
        missing = []
        for script in REQUIRED_SCRIPTS:
            try:
                result = subprocess.run(
                    ["fc-list", ":charset=4e00", "family"]
                    if script == "Han"
                    else ["fc-list", "family"],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            if result.returncode != 0:
                return None
            if not result.stdout.strip():
                missing.append(script)
        return tuple(missing)

    def capability(self) -> ConversionCapability:
        executable = self._locate()
        if executable is None:
            return ConversionCapability(
                state="converter_missing",
                converter=None,
                converter_version=None,
                missing_scripts=(),
                detail="no headless office converter is installed on this host",
            )
        version = self._version(executable)
        if version is None:
            return ConversionCapability(
                state="unverified",
                converter=executable.name,
                converter_version=None,
                missing_scripts=(),
                detail="the converter is present but did not report a version",
            )
        missing = self._missing_scripts()
        if missing is None:
            return ConversionCapability(
                state="unverified",
                converter=executable.name,
                converter_version=version,
                missing_scripts=(),
                detail="font coverage could not be checked on this host",
            )
        if missing:
            return ConversionCapability(
                state="fonts_missing",
                converter=executable.name,
                converter_version=version,
                missing_scripts=missing,
                detail="no installed font covers a script the official forms use",
            )
        return ConversionCapability(
            state="ready",
            converter=executable.name,
            converter_version=version,
            missing_scripts=(),
            detail="converter and font coverage present; output is still verified per run",
        )

    def convert(
        self,
        workbook: bytes,
        *,
        expected_sha256: str,
        verify_text: tuple[str, ...] = (),
        expected_page_count: int | None = None,
    ) -> ConvertedWorkbook:
        """Render exactly these bytes, then verify the result before returning it."""
        capability = self.capability()
        if capability.state in {"converter_missing", "fonts_missing"}:
            raise ConversionUnavailable(capability.state)
        assert capability.converter is not None
        digest = hashlib.sha256(workbook).hexdigest()
        if digest != expected_sha256:
            raise ConversionUnavailable("workbook_digest_mismatch")
        if not workbook or len(workbook) > self.limits.max_workbook_bytes:
            raise ConversionUnavailable("workbook_size_unsupported")
        if not workbook.startswith(b"PK"):
            raise ConversionUnavailable("workbook_is_not_a_package")
        executable = self._locate()
        if executable is None:
            raise ConversionUnavailable("converter_missing")
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            source = root / "workbook.xlsx"
            source.write_bytes(workbook)
            profile = root / "profile"
            command = [
                str(executable),
                "--headless",
                "--norestore",
                "--nolockcheck",
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--convert-to",
                "pdf:calc_pdf_Export",
                "--outdir",
                str(root),
                str(source),
            ]
            try:
                result = self._run(command, cwd=root)
            except subprocess.TimeoutExpired:
                raise ConversionUnavailable("conversion_timeout") from None
            except (OSError, subprocess.SubprocessError):
                raise ConversionUnavailable("conversion_failed") from None
            output = root / "workbook.pdf"
            if result.returncode != 0 or not output.is_file():
                # Converter diagnostics can quote paths and cell content.
                raise ConversionUnavailable("conversion_failed")
            content = output.read_bytes()
        if not content.startswith(b"%PDF-") or len(content) > self.limits.max_output_bytes:
            raise ConversionUnavailable("output_is_not_a_bounded_pdf")
        pages, text = _inspect(content, self.limits.max_pages)
        if expected_page_count is not None and pages != expected_page_count:
            raise ConversionUnavailable("output_page_count_differs")
        if not text.strip():
            raise ConversionUnavailable("output_has_no_extractable_text")
        for required in verify_text:
            if required and required not in text:
                # A missing heading usually means a font fell back to blank boxes.
                raise ConversionUnavailable("output_is_missing_required_text")
        return ConvertedWorkbook(
            content=content,
            page_count=pages,
            workbook_sha256=digest,
            output_sha256=hashlib.sha256(content).hexdigest(),
            converter=capability.converter,
            converter_version=capability.converter_version or "unknown",
        )


def _inspect(content: bytes, max_pages: int) -> tuple[int, str]:
    try:
        import pymupdf
    except ImportError:
        raise ConversionUnavailable("output_inspection_unavailable") from None
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
            if document.is_encrypted or not 0 < document.page_count <= max_pages:
                raise ConversionUnavailable("output_is_not_a_bounded_pdf")
            return document.page_count, "".join(page.get_text() for page in document)
    except ConversionUnavailable:
        raise
    except Exception:
        raise ConversionUnavailable("output_is_not_a_readable_pdf") from None
