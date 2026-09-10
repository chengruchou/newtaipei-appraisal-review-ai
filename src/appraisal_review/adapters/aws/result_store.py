"""Immutable result bodies; only a fenced DynamoDB reference selects a readable body."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import Any
from uuid import UUID

from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceResult
from appraisal_review.ports.jobs import JobStore


class S3ResultStore:
    def __init__(
        self,
        client: Any,
        *,
        bucket: str,
        account_id: str,
        jobs: JobStore,
        max_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if not bucket or len(account_id) != 12 or not account_id.isdigit() or max_bytes <= 0:
            raise ValueError("Invalid result storage configuration")
        self.client, self.bucket, self.account_id, self.jobs = client, bucket, account_id, jobs
        self.max_bytes = max_bytes

    def _key(self, run_id: UUID, version: int, digest: str) -> str:
        if version < 1 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        return f"results/{UUID(str(run_id))}/{version}/{digest}.json"

    async def put(self, *, run_id: UUID, result_version: int, result: ServiceResult) -> str:
        try:
            result = ServiceResult.model_validate_json(result.model_dump_json(warnings="error"))
        except Exception:
            raise ServiceFault(ServiceErrorCode.VALIDATION) from None
        if result.run.run_id != run_id or result.result_version != result_version:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        digest = content_digest(result)
        key = self._key(run_id, result_version, digest)
        data = json.dumps(
            result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        if len(data) > self.max_bytes:
            raise ServiceFault(ServiceErrorCode.VALIDATION)
        try:
            response = await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket,
                ExpectedBucketOwner=self.account_id,
                Key=key,
                Body=data,
                IfNoneMatch="*",
                ContentType="application/json",
                ServerSideEncryption="AES256",
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode(),
            )
            if not response.get("VersionId") or response["VersionId"] == "null":
                raise ServiceFault(ServiceErrorCode.EXECUTION)
        except Exception as error:
            # A content-addressed replay is safe only after verifying the existing bytes.
            response_data = getattr(error, "response", {})
            if response_data.get("Error", {}).get("Code") != "PreconditionFailed":
                raise ServiceFault(ServiceErrorCode.EXECUTION) from None
            existing = await asyncio.to_thread(self._read, key)
            if existing is None or content_digest(existing) != digest:
                raise ServiceFault(ServiceErrorCode.EXECUTION) from None
        return digest

    def _read(self, key: str) -> ServiceResult | None:
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                ExpectedBucketOwner=self.account_id,
                Key=key,
                ChecksumMode="ENABLED",
            )
            body = response["Body"]
            try:
                data = body.read(self.max_bytes + 1)
            finally:
                body.close()
            if (
                len(data) > self.max_bytes
                or len(data) != response["ContentLength"]
                or not response.get("VersionId")
                or response["VersionId"] == "null"
                or response.get("ServerSideEncryption") != "AES256"
                or response.get("ChecksumSHA256")
                != base64.b64encode(hashlib.sha256(data).digest()).decode()
            ):
                raise ValueError("Invalid stored result")
            return ServiceResult.model_validate_json(data)
        except Exception:
            raise ServiceFault(ServiceErrorCode.EXECUTION) from None

    async def get(self, *, run_id: UUID, result_version: int) -> ServiceResult | None:
        ref = await self.jobs.read_result_reference(run_id=run_id, result_version=result_version)
        if ref is None:
            return None
        result = await asyncio.to_thread(
            self._read, self._key(run_id, result_version, ref.result_digest)
        )
        if (
            result is None
            or result.run.run_id != run_id
            or result.result_version != result_version
            or content_digest(result) != ref.result_digest
        ):
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        return result
