"""Linux-only immutable encrypted files under an explicitly trusted repository root."""

from __future__ import annotations

import hashlib
import importlib
import os
import stat
import sys
from _thread import LockType
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock
from types import ModuleType
from uuid import UUID, uuid4

from appraisal_review.domain.privacy_mapping import MappingError, MappingFault
from appraisal_review.domain.privacy_models import EncryptedMappingEnvelope

MAX_ENVELOPE_BYTES = 17_000_000


def envelope_digest(envelope: EncryptedMappingEnvelope) -> str:
    return hashlib.sha256(envelope.model_dump_json().encode("utf-8")).hexdigest()


class LinuxEncryptedMappingStore:
    """No updates, arbitrary paths, recursive deletion, plaintext staging or key files.

    The owning account and workspace ancestors are trusted. This does not defend
    against a compromised owner/root, external snapshot rollback or disk forensics.
    """

    _thread: LockType
    _fd: int
    _uid: int
    _nofollow: int
    _nonblock: int
    _fcntl: ModuleType
    _root: Path

    def __init__(self, root: Path, *, workspace: Path) -> None:
        if sys.platform != "linux":
            raise MappingFault(MappingError.PLATFORM)
        self._thread = Lock()
        self._fd = -1
        self._uid = os.getuid()
        self._nofollow = os.O_NOFOLLOW
        self._nonblock = os.O_NONBLOCK
        self._fcntl = importlib.import_module("fcntl")
        try:
            if (
                not root.is_absolute()
                or not workspace.is_absolute()
                or (
                    root.resolve(strict=True) != root
                    or workspace.resolve(strict=True) != workspace
                    or root == workspace
                    or not root.is_relative_to(workspace)
                )
            ):
                raise MappingFault(MappingError.INVALID)
            self._root = root
            fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                self._directory(os.fstat(fd), private=False)
                parts = root.relative_to(workspace).parts
                for index, part in enumerate(parts):
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = child
                    self._directory(os.fstat(fd), private=index == len(parts) - 1)
                self._fd = os.dup(fd)
            finally:
                os.close(fd)
        except MappingFault:
            raise
        except Exception:
            raise MappingFault(MappingError.IO) from None

    def _directory(self, info: os.stat_result, *, private: bool) -> None:
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self._uid
            or (stat.S_IMODE(info.st_mode) & (0o077 if private else 0o022))
        ):
            raise MappingFault(MappingError.IO)

    def _file(self, info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != self._uid
            or (stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1)
        ):
            raise MappingFault(MappingError.IO)

    @staticmethod
    def _name(case_id: UUID, map_id: UUID) -> str:
        if any(type(value) is not UUID or value.version != 4 for value in (case_id, map_id)):
            raise MappingFault(MappingError.INVALID)
        return f"{case_id.hex}-{map_id.hex}.map"

    @contextmanager
    def _guard(self) -> Iterator[None]:
        if not self._thread.acquire(blocking=False):
            raise MappingFault(MappingError.CONFLICT)
        lock_fd = -1
        try:
            if self._fd < 0:
                raise MappingFault(MappingError.IO)
            current, opened = self._root.lstat(), os.fstat(self._fd)
            self._directory(current, private=True)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise MappingFault(MappingError.IO)
            lock_fd = os.open(
                ".mapping.lock", os.O_RDWR | os.O_CREAT | self._nofollow, 0o600, dir_fd=self._fd
            )
            self._file(os.fstat(lock_fd))
            self._fcntl.flock(lock_fd, self._fcntl.LOCK_EX | self._fcntl.LOCK_NB)
            yield
        except MappingFault:
            raise
        except BlockingIOError:
            raise MappingFault(MappingError.CONFLICT) from None
        except Exception:
            raise MappingFault(MappingError.IO) from None
        finally:
            if lock_fd >= 0:
                os.close(lock_fd)
            self._thread.release()

    def _read(self, name: str) -> bytes:
        fd = os.open(name, os.O_RDONLY | self._nofollow | self._nonblock, dir_fd=self._fd)
        try:
            before = os.fstat(fd)
            self._file(before)
            if not 0 < before.st_size <= MAX_ENVELOPE_BYTES:
                raise MappingFault(MappingError.INVALID)
            raw = bytearray()
            while chunk := os.read(fd, min(65536, MAX_ENVELOPE_BYTES + 1 - len(raw))):
                raw.extend(chunk)
                if len(raw) > MAX_ENVELOPE_BYTES:
                    raise MappingFault(MappingError.INVALID)
            after = os.fstat(fd)
            if len(raw) != before.st_size:
                raise MappingFault(MappingError.CONFLICT)
            linked = os.stat(name, dir_fd=self._fd, follow_symlinks=False)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or (linked.st_dev, linked.st_ino) != (after.st_dev, after.st_ino):
                raise MappingFault(MappingError.CONFLICT)
            return bytes(raw)
        finally:
            os.close(fd)

    def create(self, envelope: EncryptedMappingEnvelope) -> None:
        with self._guard():
            envelope = EncryptedMappingEnvelope.model_validate(envelope)
            name = self._name(envelope.case_id, envelope.map_id)
            raw = envelope.model_dump_json().encode("utf-8")
            if len(raw) > MAX_ENVELOPE_BYTES:
                raise MappingFault(MappingError.INVALID)
            temporary = f".{uuid4().hex}.tmp"
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | self._nofollow,
                0o600,
                dir_fd=self._fd,
            )
            identity = os.fstat(fd)
            try:
                offset = 0
                while offset < len(raw):
                    written = os.write(fd, raw[offset:])
                    if written <= 0:
                        raise MappingFault(MappingError.IO)
                    offset += written
                os.fsync(fd)
                try:
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=self._fd,
                        dst_dir_fd=self._fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    raise MappingFault(MappingError.CONFLICT) from None
            finally:
                os.close(fd)
                current = os.stat(temporary, dir_fd=self._fd, follow_symlinks=False)
                if (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino):
                    raise MappingFault(MappingError.CONFLICT)
                os.unlink(temporary, dir_fd=self._fd)
            os.fsync(self._fd)

    def read(self, case_id: UUID, map_id: UUID, *, now: datetime) -> EncryptedMappingEnvelope:
        with self._guard():
            raw = self._read(self._name(case_id, map_id))
            envelope = EncryptedMappingEnvelope.model_validate_json(raw)
            if (
                envelope.case_id != case_id
                or envelope.map_id != map_id
                or (raw != envelope.model_dump_json().encode("utf-8"))
            ):
                raise MappingFault(MappingError.AUTHENTICATION)
            if now >= envelope.expires_at:
                raise MappingFault(MappingError.EXPIRED)
            return envelope

    def delete(self, case_id: UUID, map_id: UUID, *, expected_digest: str) -> None:
        """Exact ciphertext CAS deletion, also permitted after expiry; no recursive cleanup."""
        with self._guard():
            name = self._name(case_id, map_id)
            raw = self._read(name)
            envelope = EncryptedMappingEnvelope.model_validate_json(raw)
            if (
                envelope.case_id != case_id
                or envelope.map_id != map_id
                or (hashlib.sha256(raw).hexdigest() != expected_digest)
            ):
                raise MappingFault(MappingError.CONFLICT)
            os.unlink(name, dir_fd=self._fd)
            os.fsync(self._fd)

    def close(self) -> None:
        with self._thread:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
