"""Confined acquisition and in-memory immutable originals; no plaintext staging."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from pydantic import Field

from appraisal_review.adapters.local.privacy.pdf_worker import (
    PDFInspection,
    ScanLimits,
    WorkerRequest,
)
from appraisal_review.adapters.local.privacy.process import ScanFailure, run_bounded
from appraisal_review.domain.privacy_models import LocalSourceSnapshot, PrivacyModel
from appraisal_review.domain.privacy_scan import ScanIssue


class RasterResult(PrivacyModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    png: str = Field(repr=False)


@dataclass(frozen=True)
class LocalRaster:
    width: int
    height: int
    png: bytes = field(repr=False)


class IsolatedPrivacyPDF:
    def __init__(self, work_directory: Path, limits: ScanLimits | None = None) -> None:
        if not work_directory.is_absolute() or not work_directory.is_dir():
            raise ValueError("Explicit existing local work directory required")
        self.work_directory = work_directory.resolve(strict=True)
        self.limits = ScanLimits.model_validate(limits) if limits is not None else ScanLimits()

    def _call(self, request: WorkerRequest, timeout: float | None) -> bytes:
        return run_bounded(
            [sys.executable, "-I", "-m", "appraisal_review.adapters.local.privacy.pdf_worker"],
            request.model_dump_json().encode("utf-8"),
            cwd=self.work_directory,
            timeout=min(self.limits.timeout, timeout)
            if timeout is not None
            else self.limits.timeout,
            max_output=max(
                self.limits.max_pixels * 5, self.limits.max_text * self.limits.max_pages * 8
            ),
        )

    def inspect(self, data: bytes) -> PDFInspection:
        if len(data) > self.limits.max_bytes:
            raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
        request = WorkerRequest(
            action="inspect", data=base64.b64encode(data).decode("ascii"), limits=self.limits
        )
        try:
            return PDFInspection.model_validate_json(self._call(request, None))
        except ValueError:
            raise ScanFailure(ScanIssue.INVALID_INPUT) from None

    def render(self, data: bytes, page: int, *, timeout: float) -> LocalRaster:
        if len(data) > self.limits.max_bytes:
            raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
        request = WorkerRequest(
            action="render",
            data=base64.b64encode(data).decode("ascii"),
            limits=self.limits,
            page=page,
        )
        try:
            result = RasterResult.model_validate_json(self._call(request, timeout))
            png = base64.b64decode(result.png, validate=True)
            if result.width * result.height > self.limits.max_pixels or not png.startswith(
                b"\x89PNG\r\n\x1a\n"
            ):
                raise ValueError("Invalid raster result")
            return LocalRaster(result.width, result.height, png)
        except ValueError:
            raise ScanFailure(ScanIssue.INVALID_INPUT) from None


def _read_regular(fd: int, max_bytes: int) -> bytes:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ScanFailure(ScanIssue.INVALID_INPUT)
    if not 0 < before.st_size <= max_bytes:
        raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
    chunks = bytearray()
    while chunk := os.read(fd, min(65536, max_bytes + 1 - len(chunks))):
        chunks.extend(chunk)
        if len(chunks) > max_bytes:
            raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
    after = os.fstat(fd)

    def identity(s: os.stat_result) -> tuple[int, ...]:
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    if identity(before) != identity(after) or len(chunks) != after.st_size:
        raise ScanFailure(ScanIssue.INVALID_INPUT)
    return bytes(chunks)


def confined_read(root: Path, relative: str, max_bytes: int) -> bytes:
    """Linux openat/no-follow traversal. Root and its ancestors are operator-owned.

    Windows checks are a development fallback, not an equivalent ACL/race guarantee.
    """
    parts = PurePosixPath(relative).parts
    if (
        not relative
        or not parts
        or relative.startswith("/")
        or "\\" in relative
        or ":" in relative
        or any(p in {"", ".", ".."} for p in relative.split("/"))
        or any(ord(c) < 32 for c in relative)
    ):
        raise ScanFailure(ScanIssue.INVALID_INPUT)
    try:
        if sys.platform == "linux":
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in parts[:-1]:
                    child = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                    )
                    os.close(directory)
                    directory = child
                fd = os.open(
                    parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                try:
                    return _read_regular(fd, max_bytes)
                finally:
                    os.close(fd)
            finally:
                os.close(directory)
        path = root
        for part in ("", *parts):
            path = path / part
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ScanFailure(ScanIssue.INVALID_INPUT)
        if not path.resolve(strict=True).is_relative_to(root):
            raise ScanFailure(ScanIssue.INVALID_INPUT)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ScanFailure(ScanIssue.INVALID_INPUT)
        with path.open("rb") as stream:
            return _read_regular(stream.fileno(), max_bytes)
    except OSError:
        raise ScanFailure(ScanIssue.INVALID_INPUT) from None


@dataclass(frozen=True)
class _SnapshotEntry:
    snapshot: LocalSourceSnapshot
    inspection: PDFInspection = field(repr=False)
    data: bytes = field(repr=False)
    source_file: Path = field(repr=False)


class LocalSnapshotStore:
    """Ephemeral trusted local store. Snapshot reads never reopen caller source paths."""

    def __init__(
        self, input_root: Path, pdf: IsolatedPrivacyPDF, *, max_store_bytes: int = 100_000_000
    ) -> None:
        if not input_root.is_absolute() or not input_root.is_dir() or input_root.is_symlink():
            raise ValueError("Explicit owned input root required")
        self.root = input_root.resolve(strict=True)
        self.pdf = pdf
        if not 0 < max_store_bytes <= 1_000_000_000:
            raise ValueError("Invalid snapshot storage limit")
        self.max_store_bytes = max_store_bytes
        self._entries: dict[UUID, _SnapshotEntry] = {}

    def capture(self, relative: str, *, case_id: UUID) -> LocalSourceSnapshot:
        if len(self._entries) >= 8:
            raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
        data = confined_read(self.root, relative, self.pdf.limits.max_bytes)
        if (
            len(data) + sum(len(entry.data) for entry in self._entries.values())
            > self.max_store_bytes
        ):
            raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
        inspection = self.pdf.inspect(data)
        snapshot = LocalSourceSnapshot(
            case_id=case_id,
            document_id=uuid4(),
            snapshot_id=uuid4(),
            source_revision=1,
            source_digest=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
            pages=tuple(p.geometry for p in inspection.pages),
        )
        self._entries[snapshot.snapshot_id] = _SnapshotEntry(
            snapshot, inspection, data, self.root / relative
        )
        return snapshot

    def _entry(self, snapshot: LocalSourceSnapshot) -> _SnapshotEntry:
        try:
            snapshot = LocalSourceSnapshot.model_validate(snapshot)
            entry = self._entries[snapshot.snapshot_id]
            if entry.snapshot != snapshot or hashlib.sha256(entry.data).hexdigest() != (
                snapshot.source_digest
            ):
                raise ValueError("Snapshot identity differs")
            return entry
        except (KeyError, ValueError):
            raise ScanFailure(ScanIssue.INVALID_INPUT) from None

    def read(self, snapshot: LocalSourceSnapshot) -> bytes:
        return self._entry(snapshot).data

    def inspection(self, snapshot: LocalSourceSnapshot) -> PDFInspection:
        return self._entry(snapshot).inspection

    def source_file(self, snapshot: LocalSourceSnapshot) -> Path:
        """Local provenance only; never return through a public serializer."""
        return self._entry(snapshot).source_file

    def forget(self, snapshot: LocalSourceSnapshot) -> None:
        """Release owned references; no claim of secure RAM/swap erasure."""
        self._entry(snapshot)
        del self._entries[snapshot.snapshot_id]
