"""Safe local PDF path resolution and atomic staged publication."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from appraisal_review.domain.pdf_models import (
    PDFReadError,
    PDFWriteError,
    SourceDestinationConflictError,
    UnsupportedDocumentURIError,
    document_identity,
)

FileIdentity = tuple[int, int]


def local_path_from_uri(uri: str) -> Path:
    """Convert one validated local file URI to a platform-native absolute path."""
    identity = document_identity(uri)
    if identity[0] != "file":
        raise UnsupportedDocumentURIError("Local PDF access requires a file URI")
    parts = urlsplit(uri)
    path = Path(url2pathname(unquote(parts.path)))
    if not path.is_absolute():
        raise UnsupportedDocumentURIError("Local PDF path must be absolute")
    return path


def _file_identity(path: Path, *, follow_symlinks: bool = True) -> FileIdentity:
    file_stat = path.stat(follow_symlinks=follow_symlinks)
    return (file_stat.st_dev, file_stat.st_ino)


@dataclass
class LocalWriteSession:
    """One same-filesystem temporary output with an explicit publish step."""

    source_path: Path
    destination_path: Path
    temporary_path: Path
    overwrite_existing: bool
    _source_identity: FileIdentity = field(repr=False)
    _source_sha256: bytes = field(repr=False)
    _temporary_identity: FileIdentity = field(repr=False)
    _published: bool = field(default=False, init=False, repr=False)

    @property
    def published(self) -> bool:
        return self._published

    def publish(self) -> None:
        """Atomically expose a nonempty, caller-validated temporary output."""
        if self._published:
            raise PDFWriteError("PDF output has already been published")
        self._require_owned_temporary_output()
        self._require_source_identity()
        self._reject_destination_source_conflict()
        if self.overwrite_existing:
            try:
                os.replace(self.temporary_path, self.destination_path)
            except OSError as error:
                raise PDFWriteError("PDF output could not be published") from error
            self._published = True
            return

        # A hardlink creates the destination atomically without a
        # check-then-replace race. Once it succeeds, publication succeeded;
        # failure to remove the private temporary name is cleanup, not a write
        # failure that could be reported while a destination already exists.
        try:
            os.link(self.temporary_path, self.destination_path)
        except FileExistsError as error:
            raise PDFWriteError("PDF destination already exists") from error
        except OSError as error:
            raise PDFWriteError("PDF output could not be published") from error
        self._published = True
        self.cleanup()

    def cleanup(self) -> None:
        """Remove only the temporary file created for this session."""
        try:
            if (
                _file_identity(self.temporary_path, follow_symlinks=False)
                == self._temporary_identity
            ):
                self.temporary_path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            # Cleanup must not replace the original write error. The temporary
            # name is random, scoped to the destination directory, and never
            # returned as a published result.
            return

    def _require_owned_temporary_output(self) -> None:
        try:
            temporary_stat = self.temporary_path.stat(follow_symlinks=False)
        except OSError as error:
            raise PDFWriteError("PDF temporary output is unavailable") from error
        identity = (temporary_stat.st_dev, temporary_stat.st_ino)
        if (
            not stat.S_ISREG(temporary_stat.st_mode)
            or identity != self._temporary_identity
            or temporary_stat.st_size == 0
        ):
            raise PDFWriteError("PDF temporary output is invalid")

    def _require_source_identity(self) -> None:
        try:
            if _file_identity(self.source_path) != self._source_identity:
                raise PDFReadError("PDF source identity changed during writing")
            if sha256(self.source_path.read_bytes()).digest() != self._source_sha256:
                raise PDFReadError("PDF source content changed during writing")
        except PDFReadError:
            raise
        except OSError as error:
            raise PDFReadError("PDF source became unavailable during writing") from error

    def _reject_destination_source_conflict(self) -> None:
        destination = self.destination_path
        if destination.is_symlink() and not destination.exists():
            raise PDFWriteError("PDF destination is a broken symbolic link")
        if not destination.exists():
            return
        try:
            if os.path.samefile(self.source_path, destination):
                raise SourceDestinationConflictError("PDF source and destination are aliases")
        except SourceDestinationConflictError:
            raise
        except OSError as error:
            raise PDFWriteError("PDF destination identity cannot be verified") from error


class LocalObjectAccess:
    """Prepare safe local paths without implementing the PDF writer protocol."""

    def __init__(self, *, overwrite_existing: bool = False) -> None:
        self.overwrite_existing = overwrite_existing

    @contextmanager
    def staged_write(self, source_uri: str, destination_uri: str) -> Iterator[LocalWriteSession]:
        source = local_path_from_uri(source_uri)
        destination = local_path_from_uri(destination_uri)
        source_resolved, source_identity, source_sha256 = self._validate_source(source)
        destination_resolved = self._validate_destination(source_resolved, destination)
        temporary, temporary_identity = self._create_temporary(destination_resolved)
        session = LocalWriteSession(
            source_path=source_resolved,
            destination_path=destination_resolved,
            temporary_path=temporary,
            overwrite_existing=self.overwrite_existing,
            _source_identity=source_identity,
            _source_sha256=source_sha256,
            _temporary_identity=temporary_identity,
        )
        try:
            yield session
        finally:
            session.cleanup()

    @staticmethod
    def _validate_source(source: Path) -> tuple[Path, FileIdentity, bytes]:
        try:
            resolved = source.resolve(strict=True)
            if not resolved.is_file():
                raise PDFReadError("PDF source must be a regular file")
            with resolved.open("rb") as source_file:
                source_digest = sha256(source_file.read()).digest()
            return resolved, _file_identity(resolved), source_digest
        except PDFReadError:
            raise
        except OSError as error:
            raise PDFReadError("PDF source is unavailable or unreadable") from error

    def _validate_destination(self, source: Path, destination: Path) -> Path:
        if destination.is_symlink() and not destination.exists():
            raise PDFWriteError("PDF destination is a broken symbolic link")
        try:
            parent = destination.parent.resolve(strict=True)
        except OSError as error:
            raise PDFWriteError("PDF destination parent is unavailable") from error
        if not parent.is_dir():
            raise PDFWriteError("PDF destination parent must be a directory")
        resolved = parent / destination.name
        if resolved == source:
            raise SourceDestinationConflictError("PDF source and destination are aliases")
        if resolved.exists():
            if not resolved.is_file():
                raise PDFWriteError("PDF destination must be a regular file")
            try:
                if os.path.samefile(source, resolved):
                    raise SourceDestinationConflictError("PDF source and destination are aliases")
            except SourceDestinationConflictError:
                raise
            except OSError as error:
                raise PDFWriteError("PDF destination identity cannot be verified") from error
            if not self.overwrite_existing:
                raise PDFWriteError("PDF destination already exists")
        return resolved

    @staticmethod
    def _create_temporary(destination: Path) -> tuple[Path, FileIdentity]:
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            os.close(descriptor)
            temporary = Path(raw_path)
            return temporary, _file_identity(temporary, follow_symlinks=False)
        except OSError as error:
            raise PDFWriteError("PDF temporary output could not be created") from error
