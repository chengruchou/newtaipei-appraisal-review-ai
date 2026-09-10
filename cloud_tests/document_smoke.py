"""Opt-in S3 contract probe with an exact version journal and separately authorized cleanup.

Importing this module, running --help, or running repository tests makes no AWS calls.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from appraisal_review.adapters.aws.document_storage import (
    S3DocumentConfiguration,
    S3DocumentStorage,
)
from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    ObjectKey,
    ObjectLabels,
    digest_bytes,
)
from appraisal_review.ports.document_transfer import ImmutableDocumentStorage


def exercise_storage(storage: ImmutableDocumentStorage) -> None:
    """Identical bounded creation/version/integrity contract for local, stubbed and live S3."""
    key = ObjectKey(kind="content", scope=uuid4(), identity=uuid4())
    content = b"%PDF-1.7\nsynthetic storage contract only\n%%EOF\n"
    labels = ObjectLabels(
        case_id=uuid4(),
        uploader=uuid4(),
        created_at=datetime.now(UTC),
        content_hash=digest_bytes(content),
        byte_size=len(content),
        content_type="application/pdf",
        purpose="forms",
    )
    version = storage.create(key, content, labels)
    assert version and version != "null"
    assert storage.read(key, version=version, limit=len(content)).content == content
    assert storage.read(key, version=None, limit=len(content)).version == version
    for call, code in (
        (lambda: storage.create(key, content, labels), DocumentErrorCode.CONFLICT),
        (
            lambda: storage.read(key, version=version, limit=len(content) - 1),
            DocumentErrorCode.TOO_LARGE,
        ),
    ):
        try:
            call()
        except DocumentFault as error:
            assert error.problem.code == code
        else:
            raise AssertionError("Storage contract rejection was not enforced")
    assert storage.read(key, version=version, limit=len(content)).content == content


class JournaledClient:
    """Record intent before each write and exact successful versions, never source content."""

    def __init__(self, client: Any, journal: Path) -> None:
        self.client = client
        self.journal = journal
        descriptor = os.open(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.created: list[dict[str, str]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def _record(self, record: dict[str, str]) -> None:
        with self.journal.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        intent = str(uuid4())
        self._record(
            {
                "state": "intended",
                "intent": intent,
                "bucket": kwargs["Bucket"],
                "key": kwargs["Key"],
            }
        )
        try:
            response: dict[str, Any] = self.client.put_object(**kwargs)
        except Exception as error:
            response_code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if response_code in {"PreconditionFailed", "AccessDenied"}:
                self._record({"state": "rejected", "intent": intent})
            # Ambiguous transport failures remain unresolved in the journal, not guessed away.
            raise
        version = response.get("VersionId")
        if not isinstance(version, str) or not version or version == "null":
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE)
        owned = {
            "bucket": kwargs["Bucket"],
            "key": kwargs["Key"],
            "version": version,
            "owner": kwargs["ExpectedBucketOwner"],
            "intent": intent,
        }
        self.created.append(owned)
        self._record({"state": "created", **owned})
        return response

    def cleanup(self, client: Any, config: S3DocumentConfiguration) -> int:
        cleaned = 0
        for owned in self.created:
            if (
                owned["bucket"] != config.bucket
                or owned["owner"] != config.expected_owner
                or not owned["key"].startswith(config.prefix)
            ):
                raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)
            args = {
                "Bucket": owned["bucket"],
                "Key": owned["key"],
                "VersionId": owned["version"],
                "ExpectedBucketOwner": owned["owner"],
            }
            tags = client.get_object_tagging(**args)["TagSet"]
            if {t["Key"]: t["Value"] for t in tags} != {
                "classification": "sanitized",
                "namespace": str(config.namespace),
            }:
                raise DocumentFault(DocumentErrorCode.UNAUTHORIZED)
            client.delete_object(**args)
            self._record({"state": "cleaned", **owned})
            cleaned += 1
        return cleaned


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-synthetic-cleanup", action="store_true")
    for name in (
        "profile",
        "cleanup-profile",
        "region",
        "expected-account",
        "bucket",
        "namespace",
        "journal",
    ):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    if not args.execute or not args.allow_synthetic_cleanup or args.profile == args.cleanup_profile:
        parser.error("Explicit execution and separate authorized synthetic cleanup are required")
    root = Path(__file__).resolve().parents[1]
    journal = Path(args.journal).resolve()
    if not journal.is_relative_to(root / "artifacts") or not journal.parent.is_dir():
        parser.error("Journal must be a new file in an existing repository artifacts directory")
    config = S3DocumentConfiguration(args.bucket, args.expected_account, UUID(args.namespace))

    import boto3

    runtime = boto3.Session(profile_name=args.profile, region_name=args.region)
    cleanup = boto3.Session(profile_name=args.cleanup_profile, region_name=args.region)
    runtime_identity = runtime.client("sts").get_caller_identity()
    cleanup_identity = cleanup.client("sts").get_caller_identity()

    def identity_scope(arn: str) -> str:
        return arn.rsplit("/", 1)[0] if ":assumed-role/" in arn else arn

    if (
        runtime_identity["Account"] != args.expected_account
        or cleanup_identity["Account"] != args.expected_account
        or identity_scope(runtime_identity["Arn"]) == identity_scope(cleanup_identity["Arn"])
    ):
        raise SystemExit("Designated account or separate cleanup identity check failed")
    client = JournaledClient(runtime.client("s3"), journal)
    cleaned = 0
    try:
        exercise_storage(S3DocumentStorage(client, config))
    finally:
        cleaned = client.cleanup(cleanup.client("s3"), config)
    print(f"Storage contract passed; exact owned versions cleaned: {cleaned}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Document cloud probe failed; inspect the private journal locally."
        ) from None
