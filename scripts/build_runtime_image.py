"""Build locally and verify a single ARM64 image; never authenticate, push or call AWS."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
IMAGE_TAG = re.compile(r"[a-z0-9][a-z0-9./_-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
FORBIDDEN_CONFIG_KEYS = {
    "profile",
    "profile_name",
    "aws_profile",
    "aws_default_profile",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "access_key",
    "secret_key",
    "password",
    "api_key",
    "credential",
    "credentials",
}


def private_config(path: Path) -> bytes:
    """Validate JSON without echoing operator values or passing them as build arguments."""
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("Configuration must be a private regular file (mode 0600 or 0400)")
    data = path.read_bytes()
    if len(data) > 65536:
        raise ValueError("Configuration exceeds the local build limit")
    value = json.loads(data)
    if not isinstance(value, dict) or not value:
        raise ValueError("Configuration must be a nonempty JSON object")

    def inspect(node: object) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", str(key)).lower()
                if normalized.replace("-", "_") in FORBIDDEN_CONFIG_KEYS:
                    raise ValueError("Configuration cannot contain credentials or a local profile")
                inspect(item)
        elif isinstance(node, list):
            for item in node:
                inspect(item)

    inspect(value)
    return data


def read_json_member(archive: tarfile.TarFile, name: str) -> Any:
    """Read JSON in place; never extract archive paths to the filesystem."""
    member = archive.getmember(name)
    if not member.isfile() or member.size > 2 * 1024 * 1024:
        raise ValueError("Invalid image metadata member")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError("Missing image metadata")
    return json.loads(stream.read())


def verify_archive(path: Path, image_id: str) -> dict[str, object]:
    """Check the saved image's actual config and its content digest, not build flags."""
    if not DIGEST.fullmatch(image_id):
        raise ValueError("Invalid local image identifier")
    with tarfile.open(path) as archive:
        manifest = read_json_member(archive, "manifest.json")
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError("Expected a single-image Docker manifest")
        config_name = manifest[0]["Config"]
        config = read_json_member(archive, config_name)
        stream = archive.extractfile(config_name)
        if stream is None:
            raise ValueError("Missing image config")
        config_digest = "sha256:" + hashlib.sha256(stream.read()).hexdigest()
        if config_digest != image_id:
            # The containerd image store identifies images by manifest instead of config.
            # Verify the manifest bytes and its reference to the same checked config.
            manifest_name = "blobs/sha256/" + image_id.removeprefix("sha256:")
            try:
                descriptor = read_json_member(archive, manifest_name)
                manifest_stream = archive.extractfile(manifest_name)
            except KeyError:
                raise ValueError("Exported config differs from the inspected image") from None
            if manifest_stream is None:
                raise ValueError("Missing inspected image manifest")
            manifest_digest = "sha256:" + hashlib.sha256(manifest_stream.read()).hexdigest()
            if (
                manifest_digest != image_id
                or descriptor.get("schemaVersion") != 2
                or descriptor.get("config", {}).get("digest") != config_digest
                or not descriptor.get("layers")
            ):
                raise ValueError("Exported manifest does not bind the inspected image config")
        if config.get("architecture") != "arm64" or config.get("os") != "linux":
            raise ValueError("Actual exported image must be linux/arm64")
        if not manifest[0].get("Layers"):
            raise ValueError("Image manifest contains no layers")
    return {
        "platform": "linux/arm64",
        "config_digest": config_digest,
        "exported_manifest_verified": True,
    }


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def verify_local_image(tag: str, directory: Path) -> dict[str, object]:
    metadata = json.loads(run("docker", "image", "inspect", tag))
    if not isinstance(metadata, list) or len(metadata) != 1:
        raise ValueError("Expected exactly one local image")
    image = metadata[0]
    if image.get("Architecture") != "arm64" or image.get("Os") != "linux":
        raise ValueError("Docker inspect reports an unsupported platform")
    image_id = image["Id"]
    output = directory / "image.tar"
    subprocess.run(["docker", "image", "save", "--output", str(output), image_id], check=True)
    result = verify_archive(output, image_id)
    # Refuse a tag race between inspection and export. The caller must verify again before push.
    if json.loads(run("docker", "image", "inspect", tag))[0]["Id"] != image_id:
        raise ValueError("Image tag changed during verification")
    return {**result, "image_id": image_id, "tag": tag}


def build_context(directory: Path) -> None:
    """Send only source modules and hash-locked dependencies, never the working tree."""
    source = ROOT / "src" / "appraisal_review"
    for path in sorted(source.rglob("*.py")):
        if path.is_symlink() or source not in path.resolve().parents:
            raise ValueError("Source context contains a symlink outside the package")
        destination = directory / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
    for name in (
        "Dockerfile",
        "Dockerfile.dockerignore",
        "lambda.Dockerfile",
        "lambda.Dockerfile.dockerignore",
        "requirements.lock",
    ):
        destination = directory / "infra" / "runtime" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / "infra" / "runtime" / name).read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Explicit local image tag; never latest")
    parser.add_argument("--target", choices=("runtime", "lambda"), required=True)
    parser.add_argument("--base-image", help="Target-compatible Python 3.11 base pinned by digest")
    parser.add_argument(
        "--runtime-config", type=Path, help="Private operator JSON, runtime target only"
    )
    parser.add_argument(
        "--verify-only", action="store_true", help="Recheck immediately before manual push"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Private local verification receipt"
    )
    args = parser.parse_args()
    if not IMAGE_TAG.fullmatch(args.tag) or args.tag.endswith(":latest"):
        parser.error("An explicit image tag other than latest is required")
    output = args.output.resolve()
    artifacts = ROOT / "artifacts"
    if artifacts not in output.parents or output.exists():
        parser.error("Output must be a new file under this worktree's ignored artifacts directory")
    if not args.verify_only:
        if not args.base_image or "@" not in args.base_image:
            parser.error("A digest-pinned base image is required")
        if not DIGEST.fullmatch(args.base_image.rsplit("@", 1)[-1]):
            parser.error("Invalid base image digest")
        if args.target == "runtime" and not args.runtime_config:
            parser.error("The runtime target requires a private operator configuration")
        if args.target == "lambda" and args.runtime_config:
            parser.error("The transport image must not contain the runtime execution configuration")
    try:
        # Validate before Docker receives the mount. Never put private content in a receipt.
        config = private_config(args.runtime_config) if args.runtime_config else None
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="runtime-build-", dir=artifacts) as scratch:
            directory = Path(scratch)
            if not args.verify_only:
                context = directory / "context"
                build_context(context)
                filename = "Dockerfile" if args.target == "runtime" else "lambda.Dockerfile"
                command = [
                    "docker",
                    "buildx",
                    "build",
                    "--platform",
                    "linux/arm64",
                    "--load",
                    "--provenance=false",
                    "--sbom=false",
                    "--tag",
                    args.tag,
                    "--build-arg",
                    f"BASE_IMAGE={args.base_image}",
                    "--file",
                    str(context / "infra" / "runtime" / filename),
                ]
                if config is not None:
                    private = directory / "runtime.json"
                    private.write_bytes(config)
                    private.chmod(0o600)
                    command.extend(["--secret", f"id=runtime_config,src={private}"])
                subprocess.run([*command, str(context)], check=True, cwd=ROOT)
            evidence = verify_local_image(args.tag, directory)
            evidence.update(
                {
                    "schema_version": 1,
                    "target": args.target,
                    "registry_digest": None,
                    "container_execution_tested": False,
                }
            )
            if not args.verify_only:
                evidence["source_commit"] = run("git", "rev-parse", "HEAD")
                evidence["working_tree_dirty"] = bool(run("git", "status", "--porcelain"))
                snapshot = hashlib.sha256()
                for path in sorted(context.rglob("*")):
                    if path.is_file():
                        snapshot.update(path.relative_to(context).as_posix().encode() + b"\0")
                        snapshot.update(hashlib.sha256(path.read_bytes()).digest())
                evidence["build_context_sha256"] = snapshot.hexdigest()
                if config is not None:
                    evidence["runtime_config_sha256"] = hashlib.sha256(config).hexdigest()
                evidence["base_image"] = args.base_image
                evidence["dependencies_sha256"] = hashlib.sha256(
                    (ROOT / "infra" / "runtime" / "requirements.lock").read_bytes()
                ).hexdigest()
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x") as receipt:
                output.chmod(0o600)
                json.dump(evidence, receipt, indent=2)
                receipt.write("\n")
        print(f"Verified linux/arm64 image. Local receipt: {output}")
        print("No image was pushed. The local image ID does not establish a registry digest.")
        return 0
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        tarfile.TarError,
        subprocess.CalledProcessError,
    ):
        # Build output may contain package diagnostics, but never print the private JSON ourselves.
        print("Image build or platform verification failed; no valid receipt was produced.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
