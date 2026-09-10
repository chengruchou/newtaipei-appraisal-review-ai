"""SQS outbox dispatch and bounded IAM-authenticated Runtime bridge entry points."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from appraisal_review.adapters.aws.job_store import DynamoDBJobStore
from appraisal_review.application.outbox import (
    DispatchMessage,
    JobReconciler,
    OutboxDispatcher,
    TransientDispatchError,
)
from appraisal_review.application.review_jobs import ReviewJobService
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceResult


class RuntimeDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["job-dispatch-v1"] = "job-dispatch-v1"
    job_id: UUID
    run_id: UUID
    outbox_seq: int = Field(ge=1, strict=True)
    dispatch_token: UUID
    enqueued_at: int = Field(ge=0, strict=True)

    def message(self) -> DispatchMessage:
        return DispatchMessage(**self.model_dump())


class SQSDispatchSender:
    def __init__(self, client: Any, *, queue_url: str) -> None:
        if not queue_url.startswith("https://sqs."):
            raise ValueError("Configured regional SQS URL required")
        self.client, self.queue_url = client, queue_url

    async def __call__(self, message: DispatchMessage) -> None:
        payload = RuntimeDispatch.model_validate(asdict(message)).model_dump_json()
        try:
            result = await asyncio.to_thread(
                self.client.send_message, QueueUrl=self.queue_url, MessageBody=payload
            )
            if not result.get("MessageId"):
                raise ValueError("Missing dispatch acknowledgment")
        except Exception:
            # Outbox remains durable; no raw SDK diagnostic crosses the control plane.
            raise TransientDispatchError from None


class NoResultAccess:
    """The dispatcher role cannot read or publish result bodies."""

    async def put(self, *, run_id: UUID, result_version: int, result: ServiceResult) -> str:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)

    async def get(self, *, run_id: UUID, result_version: int) -> ServiceResult | None:
        raise ServiceFault(ServiceErrorCode.CAPABILITY)


def workload_client(
    service: Literal["dynamodb", "sqs", "s3", "bedrock-agentcore"], *, read_timeout: int = 15
) -> Any:
    """Workload role only; workstation profiles and custom endpoints are not accepted."""
    import boto3
    from botocore.config import Config
    from botocore.session import Session as CoreSession

    region = os.environ.get("REVIEW_REGION", "")
    if not region or os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE"):
        raise ServiceFault(ServiceErrorCode.CAPABILITY)
    session = CoreSession()
    session.set_config_variable("ignore_configured_endpoint_urls", True)
    return boto3.Session(botocore_session=session, region_name=region).client(
        service,
        config=Config(
            connect_timeout=3,
            read_timeout=read_timeout,
            retries={"total_max_attempts": 1, "mode": "standard"},
        ),
    )


def dispatch_handler(event: object, context: object) -> dict[str, object]:
    """Scheduled recovery pass; the caller supplies no table, queue or source identity."""
    if event != {"schema_version": "reconcile-v1"}:
        raise ValueError("Invalid reconciliation event")
    try:
        jobs = DynamoDBJobStore(
            workload_client("dynamodb"), table_name=os.environ["REVIEW_JOB_TABLE"]
        )
        service = ReviewJobService(jobs, NoResultAccess())
        sender = SQSDispatchSender(workload_client("sqs"), queue_url=os.environ["REVIEW_QUEUE_URL"])
        outcome = asyncio.run(JobReconciler(OutboxDispatcher(service, sender)).run_once(limit=25))
        return asdict(outcome)
    except Exception:
        # Failing the schedule is observable; it must not report a false successful pass.
        raise RuntimeError("reconciliation_failed") from None


def bridge_record(record: dict[str, Any], client: Any, *, runtime_arn: str) -> None:
    if not runtime_arn.startswith("arn:aws:bedrock-agentcore:"):
        raise ValueError("Configured Runtime ARN required")
    body = record.get("body")
    if not isinstance(body, str) or len(body.encode()) > 2048:
        raise ValueError("Invalid bounded dispatch body")
    payload = RuntimeDispatch.model_validate_json(body)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=str(payload.dispatch_token),
        contentType="application/json",
        accept="application/json",
        payload=payload.model_dump_json().encode(),
    )
    stream = response["response"]
    try:
        data = stream.read(4097)
    finally:
        stream.close()
    if response.get("statusCode") != 200 or len(data) > 4096:
        raise ValueError("Runtime did not acknowledge durable handling")
    result = json.loads(data)
    if (
        not isinstance(result, dict)
        or set(result) != {"job_id", "run_id", "outcome"}
        or result["job_id"] != str(payload.job_id)
        or result["run_id"] != str(payload.run_id)
        or result["outcome"]
        not in {
            "published",
            "waiting_for_human",
            "cancelled",
            "superseded",
            "failed",
            "retry_scheduled",
        }
    ):
        raise ValueError("Invalid Runtime acknowledgment")


def worker_handler(event: dict[str, Any], context: object) -> dict[str, object]:
    records = event.get("Records")
    if not isinstance(records, list) or not 1 <= len(records) <= 10:
        raise ValueError("Invalid bounded SQS batch")
    # Reject malformed outer envelopes as a whole, rather than fabricating a message ID.
    if any(
        not isinstance(r, dict)
        or not isinstance(r.get("messageId"), str)
        or r.get("eventSource") != "aws:sqs"
        for r in records
    ):
        raise ValueError("Invalid SQS record")
    client = workload_client("bedrock-agentcore", read_timeout=100)
    runtime_arn = os.environ["REVIEW_RUNTIME_ARN"]
    failures = []
    for record in records:
        try:
            bridge_record(record, client, runtime_arn=runtime_arn)
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
