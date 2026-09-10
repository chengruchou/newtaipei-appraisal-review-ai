"""Private, owner-pinned S3 storage; source reads always specify a concrete VersionId."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from appraisal_review.domain.document_transfer import (
    DocumentErrorCode,
    DocumentFault,
    ObjectKey,
    ObjectLabels,
    StoredBytes,
    digest_bytes,
)


class DocumentS3Client(Protocol):
    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_bucket_encryption(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_public_access_block(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_bucket_ownership_controls(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_bucket_policy_status(self, **kwargs: Any) -> dict[str, Any]: ...
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class S3DocumentConfiguration:
    bucket: str
    expected_owner: str
    namespace: UUID

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket)
            or not re.fullmatch(r"[0-9]{12}", self.expected_owner)
            or self.namespace.version != 4
        ):
            raise ValueError("Invalid S3 document configuration")

    @property
    def prefix(self) -> str:
        return f"sanitized/{self.namespace}/"


class S3DocumentStorage:
    def __init__(self, client: DocumentS3Client, configuration: S3DocumentConfiguration) -> None:
        self.client = client
        self.configuration = configuration
        self._checked = False

    def _bucket(self) -> dict[str, str]:
        return {
            "Bucket": self.configuration.bucket,
            "ExpectedBucketOwner": self.configuration.expected_owner,
        }

    def _key(self, key: ObjectKey) -> str:
        checked = ObjectKey.model_validate_json(key.model_dump_json())
        return self.configuration.prefix + checked.relative_key()

    def check_configuration(self) -> None:
        try:
            args = self._bucket()
            versioning = self.client.get_bucket_versioning(**args)
            encryption = self.client.get_bucket_encryption(**args)
            public = self.client.get_public_access_block(**args)["PublicAccessBlockConfiguration"]
            ownership = self.client.get_bucket_ownership_controls(**args)
            policy = self.client.get_bucket_policy_status(**args)
            if (
                versioning.get("Status") != "Enabled"
                or not all(
                    public.get(k) is True
                    for k in (
                        "BlockPublicAcls",
                        "IgnorePublicAcls",
                        "BlockPublicPolicy",
                        "RestrictPublicBuckets",
                    )
                )
                or not encryption["ServerSideEncryptionConfiguration"]["Rules"]
                or any(
                    rule["ApplyServerSideEncryptionByDefault"] != {"SSEAlgorithm": "AES256"}
                    for rule in encryption["ServerSideEncryptionConfiguration"]["Rules"]
                )
                or ownership["OwnershipControls"]["Rules"]
                != [{"ObjectOwnership": "BucketOwnerEnforced"}]
                or policy["PolicyStatus"]["IsPublic"] is not False
            ):
                raise DocumentFault(DocumentErrorCode.UNAVAILABLE)
        except Exception:
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE) from None
        self._checked = True

    def _ensure(self) -> None:
        if not self._checked:
            self.check_configuration()

    @staticmethod
    def _error(error: Exception) -> DocumentFault:
        # SDK diagnostics may contain object names, endpoints or raw response bodies.
        response = getattr(error, "response", {})
        code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
        mapped = {
            "PreconditionFailed": DocumentErrorCode.CONFLICT,
            "ConditionalRequestConflict": DocumentErrorCode.CONFLICT,
            "NoSuchKey": DocumentErrorCode.NOT_FOUND,
            "NoSuchVersion": DocumentErrorCode.NOT_FOUND,
            "404": DocumentErrorCode.NOT_FOUND,
        }.get(str(code), DocumentErrorCode.UNAVAILABLE)
        return DocumentFault(mapped)

    def create(self, key: ObjectKey, content: bytes, labels: ObjectLabels) -> str:
        self._ensure()
        labels = ObjectLabels.model_validate_json(labels.model_dump_json())
        checksum = digest_bytes(content)
        if len(content) != labels.byte_size or checksum != labels.content_hash:
            raise DocumentFault(DocumentErrorCode.INTEGRITY)
        try:
            response = self.client.put_object(
                **self._bucket(),
                Key=self._key(key),
                Body=content,
                ContentType=labels.content_type,
                ServerSideEncryption="AES256",
                IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode(),
                Metadata={
                    **labels.headers(),
                    "object-id": str(key.identity),
                    "document-id": str(key.scope)
                    if key.kind in {"content", "documents"}
                    else "none",
                },
                Tagging=f"classification=sanitized&namespace={self.configuration.namespace}",
            )
        except Exception as error:
            raise self._error(error) from None
        version = response.get("VersionId")
        if not isinstance(version, str) or not version or version == "null":
            raise DocumentFault(DocumentErrorCode.UNAVAILABLE)
        return version

    def read(self, key: ObjectKey, *, version: str | None, limit: int) -> StoredBytes:
        self._ensure()
        args = {**self._bucket(), "Key": self._key(key)}
        try:
            if version is None:
                # Catalog keys are create-only under the bucket policy. Resolve then pin even
                # these reads; never return a mutable latest-object response to the service.
                version = self.client.head_object(**args).get("VersionId")
            if not isinstance(version, str) or not version or version == "null":
                raise DocumentFault(DocumentErrorCode.INTEGRITY)
            response = self.client.get_object(**args, VersionId=version, ChecksumMode="ENABLED")
            body = response["Body"]
            try:
                if response["ContentLength"] > limit:
                    raise DocumentFault(DocumentErrorCode.TOO_LARGE)
                content = body.read(limit + 1)
            finally:
                body.close()
            if len(content) > limit:
                raise DocumentFault(DocumentErrorCode.TOO_LARGE)
            expected_checksum = base64.b64encode(bytes.fromhex(digest_bytes(content))).decode()
            if (
                response.get("VersionId") != version
                or response.get("ChecksumSHA256") != expected_checksum
                or response.get("ContentLength") != len(content)
                or response.get("ServerSideEncryption") != "AES256"
            ):
                raise DocumentFault(DocumentErrorCode.INTEGRITY)
            return StoredBytes(content=content, version=version)
        except DocumentFault:
            raise
        except Exception as error:
            raise self._error(error) from None
