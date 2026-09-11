"""Competition-only worker, transfer and publication composition from guarded clients."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from appraisal_review.adapters.aws.artifacts.publication import (
    AttemptArtifactPublisher,
    DynamoDBManifestStore,
    S3PublicationObjects,
)
from appraisal_review.adapters.aws.competition_clients import (
    CompetitionAWSClients,
    CompetitionClientFault,
)
from appraisal_review.adapters.aws.document_storage import (
    S3DocumentConfiguration,
    S3DocumentStorage,
)
from appraisal_review.adapters.aws.job_store import DynamoDBJobStore
from appraisal_review.adapters.aws.result_store import S3ResultStore
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.runtime_sources import (
    CurrentPrincipalLookup,
    SnapshotBoundExecution,
)
from appraisal_review.application.runtime_worker import ReviewExecution, RuntimeWorker
from appraisal_review.ports.document_transfer import (
    DocumentAudit,
    DocumentAuthorization,
    ExportAttestationVerifier,
)


def _require(clients: CompetitionAWSClients) -> None:
    if type(clients) is not CompetitionAWSClients:
        raise CompetitionClientFault("competition_guarded_factory_required")
    clients.require_current()


def _resource_name(clients: CompetitionAWSClients, name: str, kind: str) -> str:
    binding = clients.binding(name, kind)
    assert binding.arn is not None
    if kind == "bucket":
        return binding.arn.split(":::", 1)[1]
    if kind == "table":
        return binding.arn.split(":table/", 1)[1]
    raise CompetitionClientFault("competition_resource_kind_unsupported")


def build_competition_transfer(
    clients: CompetitionAWSClients,
    *,
    bucket_binding: str,
    namespace: UUID,
    authorization: DocumentAuthorization,
    verifier: ExportAttestationVerifier,
    audit: DocumentAudit,
) -> DocumentTransferService:
    _require(clients)
    binding = clients.binding(bucket_binding, "bucket")
    configuration = S3DocumentConfiguration(
        bucket=_resource_name(clients, bucket_binding, "bucket"),
        expected_owner=clients.observed_account,
        namespace=namespace,
    )
    if configuration.prefix not in binding.allowed_prefixes:
        raise CompetitionClientFault("competition_document_prefix_not_approved")
    storage = S3DocumentStorage(
        clients.client("s3", clients.profile.primary_region, 30), configuration
    )
    return DocumentTransferService(storage, authorization, verifier, audit)


@dataclass(frozen=True)
class CompetitionPublication:
    objects: S3PublicationObjects
    manifests: DynamoDBManifestStore
    publisher: AttemptArtifactPublisher


def build_competition_publication(
    clients: CompetitionAWSClients,
    *,
    bucket_binding: str,
    manifest_table_binding: str,
    job_table_binding: str,
) -> CompetitionPublication:
    _require(clients)
    objects = S3PublicationObjects(
        clients.client("s3", clients.profile.primary_region, 30),
        bucket=_resource_name(clients, bucket_binding, "bucket"),
    )
    manifests = DynamoDBManifestStore(
        clients.client("dynamodb", clients.profile.primary_region, 30),
        table_name=_resource_name(clients, manifest_table_binding, "table"),
        job_table_name=_resource_name(clients, job_table_binding, "table"),
    )
    return CompetitionPublication(
        objects, manifests, AttemptArtifactPublisher(objects=objects, manifests=manifests)
    )


def build_competition_worker(
    clients: CompetitionAWSClients,
    *,
    execution: ReviewExecution,
    documents: DocumentTransferService,
    principals: CurrentPrincipalLookup,
    job_table_binding: str,
    result_bucket_binding: str,
) -> RuntimeWorker:
    _require(clients)
    # Reject an unrelated transfer composition, even if it implements the same port.
    storage = documents.storage
    if (
        type(storage) is not S3DocumentStorage
        or getattr(storage.client, "_owner", None) is not clients
    ):
        raise CompetitionClientFault("competition_guarded_documents_required")
    jobs = DynamoDBJobStore(
        clients.client("dynamodb", clients.profile.primary_region, 30),
        table_name=_resource_name(clients, job_table_binding, "table"),
    )
    results = S3ResultStore(
        clients.client("s3", clients.profile.primary_region, 30),
        bucket=_resource_name(clients, result_bucket_binding, "bucket"),
        account_id=clients.observed_account,
        jobs=jobs,
    )
    return RuntimeWorker(
        ReviewJobService(jobs, results),
        SnapshotBoundExecution(execution, documents, jobs, principals),
    )
