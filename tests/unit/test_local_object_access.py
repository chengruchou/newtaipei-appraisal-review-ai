from __future__ import annotations

import os
from pathlib import Path

import pytest

from appraisal_review.adapters.local.object_access import LocalObjectAccess, local_path_from_uri
from appraisal_review.domain.pdf_models import (
    PDFReadError,
    PDFWriteError,
    SourceDestinationConflictError,
    UnsupportedDocumentURIError,
)


def source_pdf(tmp_path: Path, name: str = "source file.pdf") -> Path:
    source = tmp_path / name
    source.write_bytes(b"%PDF-1.7\nsynthetic source")
    return source


def test_file_uri_round_trip_supports_unicode_and_spaces(tmp_path: Path) -> None:
    source = source_pdf(tmp_path, "範本 file.pdf")

    assert local_path_from_uri(source.as_uri()) == source
    localhost_uri = source.as_uri().replace("file:///", "file://localhost/")
    assert local_path_from_uri(localhost_uri) == source


@pytest.mark.parametrize(
    "uri",
    [
        "relative.pdf",
        "https://example.test/input.pdf",
        "s3://bucket/input.pdf",
        "file://remote/share/input.pdf",
        "file:///input.pdf?version=1",
        "file:///input.pdf#fragment",
    ],
)
def test_local_access_rejects_unsupported_uris(uri: str) -> None:
    with pytest.raises(UnsupportedDocumentURIError):
        local_path_from_uri(uri)


def test_missing_or_directory_source_fails(tmp_path: Path) -> None:
    access = LocalObjectAccess()
    destination = (tmp_path / "output.pdf").as_uri()

    with (
        pytest.raises(PDFReadError, match="unavailable or unreadable"),
        access.staged_write((tmp_path / "missing.pdf").as_uri(), destination),
    ):
        pass
    with (
        pytest.raises(PDFReadError, match="regular file"),
        access.staged_write(tmp_path.as_uri(), destination),
    ):
        pass


def test_missing_destination_parent_fails_without_creating_it(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    missing_parent = tmp_path / "missing" / "output.pdf"

    with (
        pytest.raises(PDFWriteError, match="parent is unavailable"),
        LocalObjectAccess().staged_write(source.as_uri(), missing_parent.as_uri()),
    ):
        pass

    assert not missing_parent.parent.exists()


def test_direct_and_normalized_aliases_are_rejected(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    alias_uri = (tmp_path / "child" / ".." / source.name).as_uri()

    for destination_uri in (source.as_uri(), alias_uri):
        with (
            pytest.raises(SourceDestinationConflictError),
            LocalObjectAccess(overwrite_existing=True).staged_write(
                source.as_uri(), destination_uri
            ),
        ):
            pass


def test_hardlink_alias_is_rejected(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "hardlink.pdf"
    os.link(source, destination)

    with (
        pytest.raises(SourceDestinationConflictError),
        LocalObjectAccess(overwrite_existing=True).staged_write(
            source.as_uri(), destination.as_uri()
        ),
    ):
        pass


def test_symlink_alias_is_rejected_when_supported(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "symlink.pdf"
    try:
        destination.symlink_to(source)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error.winerror}")

    with (
        pytest.raises(SourceDestinationConflictError),
        LocalObjectAccess(overwrite_existing=True).staged_write(
            source.as_uri(), destination.as_uri()
        ),
    ):
        pass


def test_successful_publish_is_atomic_and_removes_temporary_name(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "output.pdf"

    with LocalObjectAccess().staged_write(source.as_uri(), destination.as_uri()) as session:
        assert session.temporary_path.parent == destination.parent
        session.temporary_path.write_bytes(b"%PDF-1.7\nvalidated output")
        temporary = session.temporary_path
        session.publish()
        assert session.published

    assert destination.read_bytes() == b"%PDF-1.7\nvalidated output"
    assert not temporary.exists()
    assert source.read_bytes() == b"%PDF-1.7\nsynthetic source"


def test_failure_before_publish_cleans_temporary_and_leaves_no_destination(
    tmp_path: Path,
) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "output.pdf"

    with (
        pytest.raises(RuntimeError, match="render failed"),
        LocalObjectAccess().staged_write(source.as_uri(), destination.as_uri()) as session,
    ):
        temporary = session.temporary_path
        session.temporary_path.write_bytes(b"partial")
        raise RuntimeError("render failed")

    assert not destination.exists()
    assert not temporary.exists()


def test_empty_or_repeated_publication_fails(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "output.pdf"

    with LocalObjectAccess().staged_write(source.as_uri(), destination.as_uri()) as session:
        with pytest.raises(PDFWriteError, match="temporary output is invalid"):
            session.publish()
        session.temporary_path.write_bytes(b"validated")
        session.publish()
        with pytest.raises(PDFWriteError, match="already been published"):
            session.publish()


def test_existing_destination_is_rejected_by_default(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = source_pdf(tmp_path, "output.pdf")

    with (
        pytest.raises(PDFWriteError, match="already exists"),
        LocalObjectAccess().staged_write(source.as_uri(), destination.as_uri()),
    ):
        pass


def test_explicit_overwrite_replaces_destination_but_not_source(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = source_pdf(tmp_path, "output.pdf")

    with LocalObjectAccess(overwrite_existing=True).staged_write(
        source.as_uri(), destination.as_uri()
    ) as session:
        session.temporary_path.write_bytes(b"replacement")
        session.publish()

    assert destination.read_bytes() == b"replacement"
    assert source.read_bytes() == b"%PDF-1.7\nsynthetic source"


def test_destination_race_does_not_overwrite_when_disabled(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "output.pdf"

    with LocalObjectAccess().staged_write(source.as_uri(), destination.as_uri()) as session:
        session.temporary_path.write_bytes(b"validated")
        destination.write_bytes(b"racing writer")
        with pytest.raises(PDFWriteError, match="already exists"):
            session.publish()

    assert destination.read_bytes() == b"racing writer"


def test_cleanup_failure_after_atomic_link_does_not_misreport_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "output.pdf"

    with LocalObjectAccess().staged_write(source.as_uri(), destination.as_uri()) as session:
        session.temporary_path.write_bytes(b"validated")
        temporary = session.temporary_path
        original_unlink = Path.unlink

        def fail_for_temporary(path: Path, *args: object, **kwargs: object) -> None:
            if path == temporary:
                raise PermissionError("synthetic cleanup failure")
            original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_for_temporary)
        session.publish()
        assert session.published
        assert destination.read_bytes() == b"validated"
        monkeypatch.undo()
        session.cleanup()

    assert not temporary.exists()


def test_source_identity_change_blocks_publication(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "output.pdf"

    with LocalObjectAccess().staged_write(source.as_uri(), destination.as_uri()) as session:
        session.temporary_path.write_bytes(b"validated")
        replacement = source_pdf(tmp_path, "replacement.pdf")
        os.replace(replacement, source)
        with pytest.raises(PDFReadError, match="identity changed"):
            session.publish()

    assert not destination.exists()


def test_in_place_source_change_blocks_publication(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "output.pdf"

    with LocalObjectAccess().staged_write(source.as_uri(), destination.as_uri()) as session:
        session.temporary_path.write_bytes(b"validated")
        source.write_bytes(b"%PDF-1.7\nchanged in place")
        with pytest.raises(PDFReadError, match="content changed"):
            session.publish()

    assert not destination.exists()


def test_destination_changed_to_source_alias_before_publish_is_blocked(tmp_path: Path) -> None:
    source = source_pdf(tmp_path)
    destination = tmp_path / "output.pdf"

    with LocalObjectAccess(overwrite_existing=True).staged_write(
        source.as_uri(), destination.as_uri()
    ) as session:
        session.temporary_path.write_bytes(b"validated")
        os.link(source, destination)
        with pytest.raises(SourceDestinationConflictError):
            session.publish()

    assert source.read_bytes() == b"%PDF-1.7\nsynthetic source"
