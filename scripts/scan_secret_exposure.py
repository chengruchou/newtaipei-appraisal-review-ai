"""Offline, content-redacted credential pattern checks for Git and release inputs.

This is a bounded heuristic review aid, not proof that content contains no secret.
Archive entries are read in place, including deleted image layers; never extracted.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Protocol

PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
        rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{30,255})\b",
        rb"\bxox[baprs]-[A-Za-z0-9-]{20,255}\b",
        rb"\bAIza[A-Za-z0-9_-]{35}\b",
        rb"(?i)\bauthorization[\"' ]{0,4}[:=][ \t\"']{0,5}bearer[ \t]+"
        rb"[A-Za-z0-9._~+/=-]{16,2048}",
        rb"(?i)(?:aws_secret_access_key|aws_session_token|api_key|client_secret|"
        rb"password|access_token|refresh_token)[\"' ]{0,4}[:=][ \t]{0,4}"
        rb"[\"'][A-Za-z0-9_+/.=-]{16,1024}[\"']",
    )
)
MAX_BYTES = 512 * 1024 * 1024
MAX_DEPTH = 6


class Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class Scan:
    def __init__(self) -> None:
        self.findings: set[str] = set()
        self.incomplete: set[str] = set()
        self.files = 0
        self.digest = hashlib.sha256()

    def safe_path(self, path: str) -> str:
        encoded = path.encode(errors="replace")
        if any(pattern.search(encoded) for pattern in PATTERNS):
            return "redacted-path-sha256:" + hashlib.sha256(encoded).hexdigest()
        return path

    def content(self, path: str, content: bytes, depth: int = 0) -> None:
        label = self.safe_path(path)
        if label != path:
            self.findings.add(label)
        if len(content) > MAX_BYTES:
            self.incomplete.add(label)
            return
        self.files += 1
        self.digest.update(path.encode(errors="replace") + b"\0")
        self.digest.update(hashlib.sha256(content).digest())
        if any(pattern.search(content) for pattern in PATTERNS):
            self.findings.add(label)
        if depth > MAX_DEPTH:
            self.incomplete.add(label)
            return
        try:
            if content.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(content)) as compressed:
                    self.stream(path + "!gzip", compressed, depth + 1)
            elif content.startswith(b"PK\x03\x04") or zipfile.is_zipfile(io.BytesIO(content)):
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    for entry in archive.infolist():
                        if entry.is_dir():
                            continue
                        name = path + "!" + entry.filename
                        if entry.file_size > MAX_BYTES:
                            self.incomplete.add(self.safe_path(name))
                        else:
                            with archive.open(entry) as stream:
                                self.stream(name, stream, depth + 1)
            elif len(content) > 262 and content[257:262] == b"ustar":
                with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as tar_archive:
                    for tar_entry in tar_archive:
                        name = path + "!" + tar_entry.name
                        if tar_entry.issym() or tar_entry.islnk():
                            self.content(name, tar_entry.linkname.encode(), depth + 1)
                        elif tar_entry.isfile():
                            tar_stream = tar_archive.extractfile(tar_entry)
                            if tar_stream is None or tar_entry.size > MAX_BYTES:
                                self.incomplete.add(self.safe_path(name))
                            else:
                                with tar_stream:
                                    self.stream(name, tar_stream, depth + 1)
        except (OSError, EOFError, ValueError, RuntimeError, tarfile.TarError, zipfile.BadZipFile):
            self.incomplete.add(label)

    def stream(self, path: str, stream: Readable, depth: int = 0) -> None:
        content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            self.incomplete.add(self.safe_path(path))
        else:
            self.content(path, content, depth)

    def path(self, path: Path) -> None:
        if path.is_symlink():
            self.incomplete.add(self.safe_path(str(path)))
        elif path.is_dir():
            for child in sorted(path.iterdir()):
                self.path(child)
        elif path.is_file():
            try:
                with path.open("rb") as stream:
                    self.stream(str(path), stream)
            except OSError:
                self.incomplete.add(self.safe_path(str(path)))
        else:
            self.incomplete.add(self.safe_path(str(path)))

    def git(self, repository: Path, revision: str) -> None:
        def git(*args: str) -> bytes:
            return subprocess.check_output(
                ["git", "-C", str(repository), *args], stderr=subprocess.DEVNULL
            )

        # The full reachable history includes earlier removed credential content.
        objects = git("rev-list", "--objects", revision).splitlines()
        process = subprocess.Popen(
            ["git", "-C", str(repository), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None and process.stdout is not None
        try:
            for entry in objects:
                oid, _, name = entry.partition(b" ")
                process.stdin.write(oid + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().split()
                if len(header) != 3:
                    raise ValueError("Invalid Git object response")
                size = int(header[2])
                label = f"git:{oid.decode()}:{name.decode(errors='replace')}"
                if size > MAX_BYTES:
                    self.incomplete.add(self.safe_path(label))
                    remaining = size
                    while remaining:
                        chunk = process.stdout.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Incomplete Git object")
                        remaining -= len(chunk)
                else:
                    content = process.stdout.read(size)
                    if len(content) != size:
                        raise ValueError("Incomplete Git object")
                    if header[1] in (b"blob", b"commit", b"tag"):
                        self.content(label, content)
                if process.stdout.read(1) != b"\n":
                    raise ValueError("Invalid Git object separator")
        finally:
            process.stdin.close()
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            process.wait()
        # Inspect the index independently; an unstaged cleanup must not hide staged bytes.
        for entry in git("ls-files", "--stage", "-z").split(b"\0"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            mode, oid, _stage = metadata.split()
            label = "index:" + name.decode(errors="replace")
            if mode == b"160000":
                self.incomplete.add(self.safe_path(label))
            else:
                self.content(label, git("cat-file", "blob", oid.decode()))
        for name in git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(
            b"\0"
        ):
            if name:
                self.path(repository / name.decode(errors="surrogateescape"))

    def report(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "needs_review" if self.findings or self.incomplete else "no_pattern_matches",
            "scanned_items": self.files,
            "input_digest": self.digest.hexdigest(),
            "finding_paths": sorted(self.findings),
            "incomplete_paths": sorted(self.incomplete),
            "limitation": "Heuristic patterns; no validation, provider calls or cloud access.",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--path", type=Path, action="append", default=[])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.repository is None and not args.path:
        parser.error("Provide a repository or an explicit input path")
    scan = Scan()
    if args.repository is not None:
        try:
            scan.git(args.repository, args.revision)
        except (OSError, ValueError, subprocess.SubprocessError):
            scan.incomplete.add("git-history")
    for path in args.path:
        scan.path(path)
    report = scan.report()
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return int(bool(scan.findings or scan.incomplete))


if __name__ == "__main__":
    raise SystemExit(main())
