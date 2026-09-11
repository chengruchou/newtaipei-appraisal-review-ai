"""Explicit workload composition with no local success providers or implicit credentials."""

import os

from appraisal_review.adapters.aws.job_store import DynamoDBJobStore
from appraisal_review.adapters.aws.result_store import S3ResultStore
from appraisal_review.adapters.aws.runtime_jobs import workload_client
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.runtime_sources import (
    CurrentPrincipalLookup,
    SnapshotBoundExecution,
)
from appraisal_review.application.runtime_worker import ReviewExecution, RuntimeWorker
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode


def build_runtime_worker(
    *,
    execution: ReviewExecution,
    documents: DocumentTransferService,
    principals: CurrentPrincipalLookup,
) -> RuntimeWorker:
    """Reviewed deployment code supplies the missing owner-specific implementations.

    Configuration alone cannot invent a revision store, current directory, persisted
    human tasks or authorized publication provider. No dynamic import or demo fallback.
    """
    try:
        jobs = DynamoDBJobStore(
            workload_client("dynamodb"), table_name=os.environ["REVIEW_JOB_TABLE"]
        )
        results = S3ResultStore(
            workload_client("s3"),
            bucket=os.environ["REVIEW_RESULT_BUCKET"],
            account_id=os.environ["REVIEW_ACCOUNT_ID"],
            jobs=jobs,
        )
        bound = SnapshotBoundExecution(execution, documents, jobs, principals)
        return RuntimeWorker(ReviewJobService(jobs, results), bound)
    except Exception:
        raise ServiceFault(ServiceErrorCode.CAPABILITY) from None
