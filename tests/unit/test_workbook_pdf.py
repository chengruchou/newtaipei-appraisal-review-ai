"""Workbook to PDF conversion tests. No office suite is installed or invoked."""

import hashlib
import subprocess
from pathlib import Path

import pytest

from appraisal_review.adapters.local.workbook_pdf import (
    ConverterLimits,
    LocalWorkbookConverter,
)
from appraisal_review.ports.workbook_conversion import ConversionUnavailable

WORKBOOK = b"PK\x03\x04" + b"synthetic workbook package" * 8


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def make_pdf(pages: int, text: str) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 96), f"{text} {index + 1}", fontsize=14)
        return bytes(document.tobytes())


def converter(
    tmp_path,
    *,
    installed=True,
    version=b"LibreOffice 26.2.6.1 abcdef",
    output=None,
    returncode=0,
    fonts=True,
    raises=None,
    limits=None,
):
    executable = tmp_path / "soffice"
    if installed:
        executable.write_text("#!/bin/sh\n")

    def runner(command, cwd):
        if raises is not None:
            raise raises
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, version, b"")
        if output is not None:
            (Path(cwd) / "workbook.pdf").write_bytes(output)
        return subprocess.CompletedProcess(command, returncode, b"", b"")

    instance = LocalWorkbookConverter(
        executable=executable, runner=runner, limits=limits or ConverterLimits()
    )
    instance._missing_scripts = staticmethod(lambda: () if fonts else ("Han",))  # type: ignore[method-assign]
    return instance


def test_a_host_without_a_converter_reports_missing_and_refuses(tmp_path):
    instance = converter(tmp_path, installed=False)
    capability = instance.capability()
    assert capability.state == "converter_missing" and not capability.ready
    assert capability.converter is None
    with pytest.raises(ConversionUnavailable, match="converter_missing"):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))


def test_a_host_without_fonts_for_the_form_script_refuses(tmp_path):
    instance = converter(tmp_path, fonts=False)
    capability = instance.capability()
    assert capability.state == "fonts_missing" and capability.missing_scripts == ("Han",)
    with pytest.raises(ConversionUnavailable, match="fonts_missing"):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))


def test_an_unverifiable_converter_is_not_reported_as_ready(tmp_path):
    instance = converter(tmp_path, version=b"no version here")
    capability = instance.capability()
    assert capability.state == "unverified" and capability.converter_version is None


def test_unknown_font_coverage_is_not_reported_as_ready(tmp_path):
    instance = converter(tmp_path)
    instance._missing_scripts = staticmethod(lambda: None)  # type: ignore[method-assign]
    assert instance.capability().state == "unverified"


def test_a_ready_host_converts_and_pins_both_digests(tmp_path):
    pdf = make_pdf(2, "table 3 sheet")
    instance = converter(tmp_path, output=pdf)
    assert instance.capability().ready
    result = instance.convert(
        WORKBOOK,
        expected_sha256=digest(WORKBOOK),
        verify_text=("table 3 sheet",),
        expected_page_count=2,
    )
    assert result.page_count == 2
    assert result.workbook_sha256 == digest(WORKBOOK)
    assert result.output_sha256 == digest(pdf)
    assert result.converter_version == "26.2.6.1"
    assert result.content.startswith(b"%PDF-")


def test_a_workbook_that_is_not_the_approved_bytes_is_refused(tmp_path):
    instance = converter(tmp_path, output=make_pdf(1, "x"))
    with pytest.raises(ConversionUnavailable, match="digest_mismatch"):
        instance.convert(WORKBOOK, expected_sha256="e" * 64)


@pytest.mark.parametrize("content", [b"", b"%PDF-not a workbook", b"plain text"])
def test_input_that_is_not_a_workbook_package_is_refused(tmp_path, content):
    instance = converter(tmp_path, output=make_pdf(1, "x"))
    with pytest.raises(ConversionUnavailable):
        instance.convert(content, expected_sha256=digest(content))


def test_an_oversized_workbook_is_refused(tmp_path):
    instance = converter(
        tmp_path, output=make_pdf(1, "x"), limits=ConverterLimits(max_workbook_bytes=8)
    )
    with pytest.raises(ConversionUnavailable, match="size_unsupported"):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))


def test_a_failed_or_silent_conversion_is_refused_without_diagnostics(tmp_path):
    instance = converter(tmp_path, output=make_pdf(1, "x"), returncode=1)
    with pytest.raises(ConversionUnavailable, match=r"^conversion_failed$"):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))
    silent = converter(tmp_path, output=None)
    with pytest.raises(ConversionUnavailable, match=r"^conversion_failed$"):
        silent.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))


def test_a_timeout_is_reported_as_a_timeout(tmp_path):
    instance = converter(tmp_path, raises=subprocess.TimeoutExpired("soffice", 1))
    with pytest.raises(ConversionUnavailable, match="conversion_timeout"):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))


@pytest.mark.parametrize("output", [b"not a pdf at all", b"%PDF-1.7 truncated"])
def test_output_that_is_not_a_readable_pdf_is_refused(tmp_path, output):
    instance = converter(tmp_path, output=output)
    with pytest.raises(ConversionUnavailable):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))


def test_a_page_count_that_differs_from_the_expectation_is_refused(tmp_path):
    instance = converter(tmp_path, output=make_pdf(3, "sheet"))
    with pytest.raises(ConversionUnavailable, match="page_count_differs"):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK), expected_page_count=1)


def test_a_pdf_with_no_extractable_text_is_refused(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as document:
        document.new_page(width=595, height=842)
        blank = bytes(document.tobytes())
    instance = converter(tmp_path, output=blank)
    with pytest.raises(ConversionUnavailable, match="no_extractable_text"):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))


def test_a_missing_required_heading_is_refused(tmp_path):
    instance = converter(tmp_path, output=make_pdf(1, "wrong heading"))
    with pytest.raises(ConversionUnavailable, match="missing_required_text"):
        instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK), verify_text=("表3區段勘查表",))


def test_the_conversion_runs_headless_in_a_private_profile(tmp_path):
    seen: list[list[str]] = []
    pdf = make_pdf(1, "sheet")
    executable = tmp_path / "soffice"
    executable.write_text("#!/bin/sh\n")

    def runner(command, cwd):
        seen.append(command)
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, b"LibreOffice 26.2.6.1", b"")
        (Path(cwd) / "workbook.pdf").write_bytes(pdf)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    instance = LocalWorkbookConverter(executable=executable, runner=runner)
    instance._missing_scripts = staticmethod(lambda: ())  # type: ignore[method-assign]
    instance.convert(WORKBOOK, expected_sha256=digest(WORKBOOK))
    command = seen[-1]
    assert "--headless" in command and "--norestore" in command
    assert any(part.startswith("-env:UserInstallation=file://") for part in command)
    assert "pdf:calc_pdf_Export" in command
    # No workbook path or content is passed through a shell.
    assert all(isinstance(part, str) for part in command)


def test_the_real_host_capability_is_reported_honestly():
    """This asserts nothing about the host, only that the report is self-consistent."""
    capability = LocalWorkbookConverter().capability()
    assert capability.state in {"ready", "converter_missing", "fonts_missing", "unverified"}
    assert capability.ready == (capability.state == "ready")
    if capability.state == "converter_missing":
        assert capability.converter is None and capability.converter_version is None
