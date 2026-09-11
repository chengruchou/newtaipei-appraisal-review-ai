"""Version-pinned objects, authoritative transactional publication and downloads.

Clients are injected. No account calls, credential discovery, resource creation,
permission grants or background threads occur on import or construction.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from boto3.dynamodb.conditions import Attr, ConditionBase, ConditionExpressionBuilder
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import BotoCoreError
from pydantic import ValidationError

from appraisal_review.adapters.aws.storage.s3_object_store import S3ObjectStore
from appraisal_review.adapters.local.artifact_publication import (
    AttemptArtifactPublisher as CorePublisher,
)
from appraisal_review.adapters.local.artifact_publication import (
    CommittedResultResolver as CoreResolver,
)
from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.artifact_publication import (
    CommittedManifest,
    ManifestCandidate,
    PublicationError,
    PublishedArtifact,
)
from appraisal_review.domain.service_contracts import Permission, RunReference
from appraisal_review.ports.artifact_publication import (
    ArtifactObjectStore,
    ManifestRepository,
    PublicationAttempt,
)


def _marshal(value: dict[str, Any]) -> dict[str, Any]:
    return {key: TypeSerializer().serialize(item) for key, item in value.items()}


def _unmarshal(value: dict[str, Any]) -> dict[str, Any]:
    return {key: TypeDeserializer().deserialize(item) for key, item in value.items()}


def _condition(condition: ConditionBase) -> dict[str, Any]:
    built = ConditionExpressionBuilder().build_expression(condition)
    result: dict[str, Any] = {
        "ConditionExpression": built.condition_expression,
        "ExpressionAttributeNames": built.attribute_name_placeholders,
    }
    if built.attribute_value_placeholders:
        result["ExpressionAttributeValues"] = _marshal(built.attribute_value_placeholders)
    return result


class DynamoDBManifestStore:
    """Use a low-level client, including for all atomic job authority conditions.

    The publication table uses pk/sk. The job table is the existing job-store-v1
    table; its schema is read-only here. Independent approval/access rows must be
    managed by the trusted authorization service, never by a worker role.
    """

    def __init__(
        self,
        client: Any,
        *,
        table_name: str,
        job_table_name: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not table_name or not job_table_name:
            raise ValueError("Publication and authoritative job tables are required")
        self.client, self.table_name, self.job_table_name = client, table_name, job_table_name
        self.clock = clock

    @staticmethod
    def manifest_key(case_id: str, run_id: UUID) -> dict[str, str]:
        return {"pk": f"ARTIFACT#{case_id}#{run_id}", "sk": "MANIFEST"}

    @staticmethod
    def approval_key(run: RunReference, actor_id: str) -> dict[str, str]:
        return {"pk": f"PUBLICATION#{run.revision.case_id}#{run.run_id}", "sk": f"GRANT#{actor_id}"}

    @staticmethod
    def access_key(case_id: str, actor_id: str) -> dict[str, str]:
        return {"pk": f"CASE#{case_id}", "sk": f"ACCESS#{actor_id}"}

    def _call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        try:
            result: dict[str, Any] = getattr(self.client, operation)(**kwargs)
            return result
        except self.client.exceptions.TransactionCanceledException as error:
            reasons = error.response.get("CancellationReasons", [])
            if any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons):
                raise PublicationError("stale_publication") from None
            raise PublicationError("manifest_store_unavailable") from None
        except (BotoCoreError, self.client.exceptions.ClientError, TimeoutError):
            raise PublicationError("manifest_store_unavailable") from None

    def _get(self, table: str, key: dict[str, str]) -> dict[str, Any] | None:
        value = self._call("get_item", TableName=table, Key=_marshal(key), ConsistentRead=True)
        return _unmarshal(value["Item"]) if value.get("Item") else None

    def read(self, case_id: str, run_id: UUID) -> CommittedManifest | None:
        row = self._get(self.table_name, self.manifest_key(case_id, run_id))
        if row is None:
            return None
        try:
            manifest = CommittedManifest.model_validate_json(row["manifest"])
            if (
                manifest.candidate.run.revision.case_id != case_id
                or manifest.candidate.run.run_id != run_id
                or row["manifest_digest"] != manifest.manifest_digest
                or row["fencing_token"] != manifest.fencing_token
            ):
                raise ValueError("Manifest identity mismatch")
            return manifest
        except (ValidationError, ValueError, TypeError, KeyError):
            raise PublicationError("manifest_corrupt") from None

    def authorize(self, principal: Principal, case_id: str, permission: Permission) -> int:
        principal.require(case_id, permission)
        row = self._get(self.table_name, self.access_key(case_id, principal.actor.actor_id))
        now = int(self.clock())
        if (
            row is None
            or row.get("active") is not True
            or permission.value not in row.get("permissions", [])
            or row.get("expires_at", 0) <= now
        ):
            raise PublicationError("publication_unauthorized")
        return int(row["expires_at"])

    def _check(self, table: str, key: dict[str, str], condition: ConditionBase) -> dict[str, Any]:
        return {
            "ConditionCheck": {"TableName": table, "Key": _marshal(key), **_condition(condition)}
        }

    def commit(
        self,
        candidate: ManifestCandidate,
        *,
        fencing_token: int,
        principal: Principal,
        attempt: PublicationAttempt,
    ) -> CommittedManifest:
        candidate = ManifestCandidate.model_validate_json(candidate.model_dump_json())
        case_id, run_id = candidate.run.revision.case_id, candidate.run.run_id
        self.authorize(principal, case_id, Permission.PUBLISH)
        if (
            type(fencing_token) is not int
            or fencing_token < 1
            or type(attempt.expected_result_version) is not int
            or attempt.expected_result_version < 0
            or candidate.result_version != attempt.expected_result_version + 1
        ):
            raise PublicationError("stale_publication")
        existing = self.read(case_id, run_id)
        if existing is not None:
            if existing.fencing_token > fencing_token:
                raise PublicationError("stale_publication")
            if existing.fencing_token == fencing_token and existing.candidate != candidate:
                raise PublicationError("manifest_conflict")
        job_key = {"pk": f"JOB#{attempt.job_id}", "sk": "META"}
        run_key = {"pk": f"RUN#{run_id}", "sk": "META"}
        # Read documents for exact source binding. The final transaction binds the
        # complete observed document list as well as the immutable revision.
        run = self._get(self.job_table_name, run_key)
        if run is None:
            raise PublicationError("stale_publication")
        documents = run.get("documents", [])
        try:
            sources = {(d["document_id"], d["version"], d["content_hash"]) for d in documents}
        except (KeyError, TypeError):
            raise PublicationError("artifact_evidence_missing") from None
        if not sources or any(
            {(s.document_id, s.version, s.content_hash) for s in artifact.source_versions}
            != sources
            for artifact in candidate.artifacts
        ):
            raise PublicationError("artifact_evidence_missing")
        now = int(self.clock())
        live_job = (
            Attr("case_id").eq(case_id)
            & Attr("current_run_id").eq(str(run_id))
            & Attr("status").eq("running")
            & Attr("cancel_requested").eq(False)
        )
        live_run = (
            Attr("job_id").eq(str(attempt.job_id))
            & Attr("run_id").eq(str(run_id))
            & Attr("revision").eq(candidate.run.revision.model_dump(mode="json"))
            & Attr("documents").eq(documents)
            & Attr("status").eq("running")
            & Attr("attempt_id").eq(str(candidate.run.attempt_id))
            & Attr("lease_owner").eq(str(attempt.owner))
            & Attr("lease_expires_at").gt(now)
            & Attr("fencing_token").eq(fencing_token)
            & Attr("result_version").eq(attempt.expected_result_version)
        )
        attempt_condition = (
            Attr("job_id").eq(str(attempt.job_id))
            & Attr("run_id").eq(str(run_id))
            & Attr("attempt_id").eq(str(candidate.run.attempt_id))
            & Attr("owner").eq(str(attempt.owner))
            & Attr("fencing_token").eq(fencing_token)
            & Attr("outcome").eq("running")
            & Attr("closed_at").not_exists()
        )
        manifest = CommittedManifest(
            candidate=candidate, fencing_token=fencing_token, manifest_digest=candidate.digest()
        )
        checks = [
            self._check(self.job_table_name, job_key, live_job),
            self._check(self.job_table_name, run_key, live_run),
            self._check(
                self.job_table_name,
                {"pk": run_key["pk"], "sk": f"ATTEMPT#{candidate.run.attempt_id}"},
                attempt_condition,
            ),
            self._check(
                self.table_name,
                self.access_key(case_id, principal.actor.actor_id),
                Attr("active").eq(True)
                & Attr("permissions").contains(Permission.PUBLISH.value)
                & Attr("expires_at").gt(now),
            ),
            self._check(
                self.table_name,
                self.approval_key(candidate.run, principal.actor.actor_id),
                Attr("active").eq(True)
                & Attr("manifest_digest").eq(candidate.digest())
                & Attr("expires_at").gt(now),
            ),
        ]
        checks.append(
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": _marshal(
                        {
                            **self.manifest_key(case_id, run_id),
                            "manifest": manifest.model_dump_json(),
                            "manifest_digest": manifest.manifest_digest,
                            "fencing_token": fencing_token,
                        }
                    ),
                    **_condition(
                        Attr("pk").not_exists()
                        | Attr("fencing_token").lt(fencing_token)
                        | (
                            Attr("fencing_token").eq(fencing_token)
                            & Attr("manifest_digest").eq(manifest.manifest_digest)
                        )
                    ),
                }
            }
        )
        try:
            self._call("transact_write_items", TransactItems=checks)
        except PublicationError as error:
            # Distinguish an actual same-token content race from loss of authority.
            # An uncertain response is never converted into success; exact retry
            # reruns all current authority conditions and the idempotent put.
            if error.code == "stale_publication":
                current = self.read(case_id, run_id)
                if (
                    current
                    and current.fencing_token == fencing_token
                    and current.candidate != candidate
                ):
                    raise PublicationError("manifest_conflict") from None
            raise
        return manifest


def _fetch(client: Any, bucket: str, artifact: PublishedArtifact) -> bytes:
    if not artifact.object_version or artifact.object_version == "null":
        raise PublicationError("artifact_version_missing")
    for attempt in range(2):
        try:
            response = client.get_object(
                Bucket=bucket, Key=artifact.key, VersionId=artifact.object_version
            )
            with response["Body"] as body:
                data = body.read(artifact.size_bytes + 1)
            if response.get("ContentType") != "application/pdf":
                raise PublicationError("artifact_mismatch")
            return bytes(data)
        except PublicationError:
            raise
        except (BotoCoreError, TimeoutError):
            if attempt:
                raise PublicationError("artifact_store_unavailable") from None
        except client.exceptions.ClientError:
            raise PublicationError("artifact_store_unavailable") from None
    raise AssertionError("Unreachable bounded fetch")


class S3PublicationObjects:
    """Concrete immutable-version object adapter for the provider-neutral core."""

    def __init__(self, client: Any, *, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    def create(self, key: str, data: bytes) -> str:
        replay = False
        try:
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType="application/pdf",
                IfNoneMatch="*",
            )
        except self.client.exceptions.ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
            }:
                raise PublicationError("artifact_store_unavailable") from None
            replay = True
            try:
                response = self.client.head_object(Bucket=self.bucket, Key=key)
            except (BotoCoreError, self.client.exceptions.ClientError, TimeoutError):
                raise PublicationError("artifact_store_unavailable") from None
        except (BotoCoreError, TimeoutError):
            raise PublicationError("artifact_store_unavailable") from None
        version = response.get("VersionId")
        if not isinstance(version, str) or not version or version == "null":
            raise PublicationError("artifact_version_missing")
        if replay:
            try:
                existing = self.client.get_object(Bucket=self.bucket, Key=key, VersionId=version)
                with existing["Body"] as body:
                    same_bytes = body.read(len(data) + 1) == data
                if not same_bytes or existing.get("ContentType") != "application/pdf":
                    raise PublicationError("artifact_mismatch")
            except (BotoCoreError, self.client.exceptions.ClientError, TimeoutError):
                raise PublicationError("artifact_store_unavailable") from None
        return version

    def read(self, artifact: PublishedArtifact) -> bytes:
        return _fetch(self.client, self.bucket, artifact)


class AttemptArtifactPublisher(CorePublisher):
    """Compatibility composition for the S3 object adapter; core has no AWS dependency."""

    def __init__(
        self,
        *,
        manifests: ManifestRepository,
        objects: ArtifactObjectStore | None = None,
        object_store: S3ObjectStore | None = None,
        result_bucket: str | None = None,
        work_directory: Path | None = None,
    ) -> None:
        if objects is None:
            if object_store is None or result_bucket is None or object_store.overwrite_existing:
                raise ValueError("Immutable publication objects are required")
            objects = S3PublicationObjects(object_store.client, bucket=result_bucket)
        super().__init__(objects=objects, manifests=manifests)
        self.object_store = object_store


class CommittedResultResolver(CoreResolver):
    """S3 download composition; all authorization and verification stay in the core."""

    def __init__(
        self,
        *,
        manifests: ManifestRepository,
        objects: ArtifactObjectStore | None = None,
        object_store: S3ObjectStore | None = None,
        presigner: Any = None,
        result_bucket: str | None = None,
        work_directory: Path | None = None,
    ) -> None:
        if objects is None:
            if object_store is None or result_bucket is None:
                raise ValueError("Publication objects are required")
            objects = S3PublicationObjects(object_store.client, bucket=result_bucket)

        def issue(artifact: PublishedArtifact, expires: int) -> str:
            if presigner is None or result_bucket is None:
                raise PublicationError("download_issuer_unavailable")
            url: str = presigner.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": result_bucket,
                    "Key": artifact.key,
                    "VersionId": artifact.object_version,
                },
                ExpiresIn=expires,
            )
            return url

        super().__init__(
            objects=objects,
            manifests=manifests,
            download_issuer=issue,
            work_directory=work_directory,
        )
