"""Release checks retain paths without disclosing matched credential content."""

import gzip
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import scan_secret_exposure as scanner


def synthetic_value():
    return b"AK" + b"IA" + b"X" * 16


def tar_bytes(name, content):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


@pytest.mark.parametrize("wrapper", ["plain", "gzip", "zip", "layer"])
def test_redacts_content_in_logs_and_nested_archive_members(wrapper):
    value = synthetic_value()
    payload = b"credential=" + value
    if wrapper == "gzip":
        payload = gzip.compress(payload)
    elif wrapper == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("job.log", payload)
        payload = buffer.getvalue()
    elif wrapper == "layer":
        # A later whiteout must not conceal secret bytes in an earlier image layer.
        layer = tar_bytes("removed.env", payload)
        payload = tar_bytes("layer.tar", layer)
    scan = scanner.Scan()
    scan.content("release-input", payload)
    report = scan.report()
    assert report["status"] == "needs_review"
    assert report["finding_paths"]
    assert value.decode() not in json.dumps(report)


def test_git_scan_includes_removed_content(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args])

    git("init", "-q")
    git("config", "user.name", "Fixture Reviewer")
    git("config", "user.email", "fixture@example.invalid")
    path = tmp_path / "configuration.txt"
    path.write_bytes(synthetic_value())
    git("add", path.name)
    git("commit", "-qm", "Add fixture")
    path.write_text("removed\n")
    git("add", path.name)
    git("commit", "-qm", "Remove fixture")
    scan = scanner.Scan()
    scan.git(tmp_path, "HEAD")
    report = scan.report()
    assert report["finding_paths"]
    assert not report["incomplete_paths"]
    assert any("configuration.txt" in path for path in report["finding_paths"])
    assert synthetic_value().decode() not in json.dumps(report)


def test_git_scan_separately_checks_index_and_working_files(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args])

    git("init", "-q")
    git("config", "user.name", "Fixture Reviewer")
    git("config", "user.email", "fixture@example.invalid")
    path = tmp_path / "configuration.txt"
    path.write_text("clean\n")
    git("add", path.name)
    git("commit", "-qm", "Add fixture")
    path.write_bytes(synthetic_value())
    git("add", path.name)
    path.write_text("clean working file\n")
    untracked = tmp_path / "untracked.txt"
    untracked.write_bytes(synthetic_value())
    scan = scanner.Scan()
    scan.git(tmp_path, "HEAD")
    report = scan.report()
    assert "index:configuration.txt" in report["finding_paths"]
    assert str(untracked) in report["finding_paths"]
    assert synthetic_value().decode() not in json.dumps(report)


def test_oversized_or_missing_inputs_block_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "MAX_BYTES", 8)
    scan = scanner.Scan()
    scan.stream("large", io.BytesIO(b"x" * 9))
    scan.path(tmp_path / "missing")
    assert scan.report()["status"] == "needs_review"
    assert len(scan.report()["incomplete_paths"]) == 2


def test_corrupt_archive_cannot_appear_clean():
    scan = scanner.Scan()
    scan.content("broken.zip", b"PK\x03\x04broken")
    assert scan.report()["incomplete_paths"] == ["broken.zip"]


def test_symlink_is_not_followed_and_archive_path_is_not_extracted(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(synthetic_value())
    link = tmp_path / "link"
    link.symlink_to(outside)
    scan = scanner.Scan()
    scan.path(link)
    assert scan.report()["incomplete_paths"] == [str(link)]
    assert not scan.report()["finding_paths"]
    scan.content("archive", tar_bytes("../../escape", synthetic_value()))
    assert scan.report()["finding_paths"]
    assert not (tmp_path / "escape").exists()


def test_filename_with_credential_is_also_redacted():
    scan = scanner.Scan()
    scan.content(synthetic_value().decode(), b"safe content")
    report = scan.report()
    assert synthetic_value().decode() not in json.dumps(report)
    assert report["finding_paths"][0].startswith("redacted-path-sha256:")


def test_clean_scope_is_only_reported_as_no_pattern_matches(tmp_path: Path):
    path = tmp_path / "safe.txt"
    path.write_text("Use runtime IAM authentication.\n")
    scan = scanner.Scan()
    scan.path(path)
    assert scan.report()["status"] == "no_pattern_matches"
    assert scan.report()["scanned_items"] == 1
