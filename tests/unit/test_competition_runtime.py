"""Real SDK localhost wire tests and independent central budget workers; no AWS."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import multiprocessing
import runpy
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from moto import mock_aws
from test_competition_preflight import ROLE, ready_profile

from appraisal_review.adapters.aws.competition_budget import (
    CompetitionBudgetFault,
    DynamoDBCompetitionBudget,
    budget_seed_item,
)
from appraisal_review.adapters.aws.competition_clients import (
    CompetitionAWSClients,
    CompetitionClientFault,
)
from appraisal_review.adapters.aws.competition_runtime import (
    build_competition_publication,
    build_competition_transfer,
    build_competition_worker,
)
from appraisal_review.adapters.aws.competition_wire import CompetitionWireFault
from appraisal_review.adapters.aws.document_storage import (
    S3DocumentConfiguration,
    S3DocumentStorage,
)
from appraisal_review.adapters.aws.dynamodb_model_dispatch import DynamoDBModelDispatchStore
from appraisal_review.adapters.local.sqlite_model_dispatch import SqliteModelDispatchStore
from appraisal_review.application.document_transfer import DocumentTransferService
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.application.runtime_sources import SnapshotBoundExecution
from appraisal_review.domain.competition_data import CompetitionDataFault, DataPart
from appraisal_review.domain.competition_profile import CompetitionProfile
from appraisal_review.ports.model_dispatch import (
    DispatchGuard,
    dispatch_async_authority,
    dispatch_authority,
    dispatch_guard,
)


def runtime_profile(**changes: Any) -> CompetitionProfile:
    data = ready_profile().model_dump(mode="json")
    data["iam_actions"] += [
        "s3:PutObject",
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:GetBucketVersioning",
        "s3:GetEncryptionConfiguration",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetBucketOwnershipControls",
        "s3:GetBucketPolicyStatus",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:ConditionCheckItem",
    ]
    data["resources"] += [
        {
            "name": "evidence",
            "kind": "bucket",
            "mode": "reuse",
            "owner": "synthetic",
            "arn": "arn:aws:s3:::synthetic-evidence",
            "allowed_prefixes": ["approved/", "results/"],
            "stop_method": "retain_evidence",
            "stop_verification_reference": "synthetic-only",
        },
        {
            "name": "jobs",
            "kind": "table",
            "mode": "reuse",
            "owner": "synthetic",
            "arn": "arn:aws:dynamodb:us-east-1:123456789012:table/jobs",
            "allowed_indexes": ["job-recovery"],
            "stop_method": "retain_evidence",
            "stop_verification_reference": "synthetic-only",
        },
        {
            "name": "central",
            "kind": "table",
            "mode": "reuse",
            "owner": "synthetic",
            "arn": "arn:aws:dynamodb:us-east-1:123456789012:table/shared",
            "stop_method": "retain_evidence",
            "stop_verification_reference": "synthetic-only",
        },
    ]
    data.update(changes)
    return CompetitionProfile.model_validate(data)


class SyntheticAdmission:
    """Explicit test authority; never used by runtime construction by default."""

    def __init__(self) -> None:
        self.parts: list[tuple[DataPart, ...]] = []
        self.denied = False
        self.on_check: Any = None

    def check(self, parts: tuple[DataPart, ...]) -> None:
        self.parts.append(parts)
        if self.on_check:
            self.on_check(parts)
        if self.denied or any(b"prohibited" in p.content for p in parts):
            raise CompetitionDataFault("competition_data_unreviewed")


@contextmanager
def endpoint(*, failures: int = 0, ddb=None, objects=None):
    requests = []
    payload = b"synthetic-object"

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": body,
                }
            )
            failed = len(requests) <= failures
            status = 500 if failed else 200
            extra = {}
            target = self.headers.get("x-amz-target", "")
            if target:
                result = b'{"Item":{}}'
                content_type = "application/x-amz-json-1.0"
                if ddb is not None:
                    operation = target.split(".")[-1]
                    method = next(
                        k for k, v in ddb.meta.method_to_api_mapping.items() if v == operation
                    )
                    try:
                        response = getattr(ddb, method)(**json.loads(body))
                        response.pop("ResponseMetadata", None)
                        result = json.dumps(response).encode()
                    except ddb.exceptions.ClientError as error:
                        status = 400
                        result = json.dumps(
                            {"__type": error.response["Error"]["Code"], "message": "synthetic"}
                        ).encode()
            elif self.command == "GET":
                result = payload
                content_type = "application/pdf"
            else:
                result = b""
                content_type = "application/xml"
            if objects is not None:
                path = unquote(urlsplit(self.path).path)
                if path in objects:
                    version, stored = objects[path]
                    result = stored
                    extra = {
                        "x-amz-version-id": version,
                        "x-amz-checksum-sha256": base64.b64encode(
                            hashlib.sha256(stored).digest()
                        ).decode(),
                        "x-amz-server-side-encryption": "AES256",
                    }
                else:
                    configuration = {
                        "versioning": (
                            "<VersioningConfiguration>"
                            "<Status>Enabled</Status>"
                            "</VersioningConfiguration>"
                        ),
                        "encryption": (
                            "<ServerSideEncryptionConfiguration>"
                            "<Rule>"
                            "<ApplyServerSideEncryptionByDefault>"
                            "<SSEAlgorithm>AES256</SSEAlgorithm>"
                            "</ApplyServerSideEncryptionByDefault>"
                            "</Rule>"
                            "</ServerSideEncryptionConfiguration>"
                        ),
                        "publicAccessBlock": (
                            "<PublicAccessBlockConfiguration>"
                            "<BlockPublicAcls>true</BlockPublicAcls>"
                            "<IgnorePublicAcls>true</IgnorePublicAcls>"
                            "<BlockPublicPolicy>true</BlockPublicPolicy>"
                            "<RestrictPublicBuckets>true</RestrictPublicBuckets>"
                            "</PublicAccessBlockConfiguration>"
                        ),
                        "ownershipControls": (
                            "<OwnershipControls>"
                            "<Rule>"
                            "<ObjectOwnership>BucketOwnerEnforced</ObjectOwnership>"
                            "</Rule>"
                            "</OwnershipControls>"
                        ),
                        "policyStatus": "<PolicyStatus><IsPublic>false</IsPublic></PolicyStatus>",
                    }
                    query = urlsplit(self.path).query
                    if query in configuration:
                        result = configuration[query].encode()
            if failed:
                result = b"<Error><Code>InternalError</Code><Message>synthetic</Message></Error>"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(result) if self.command != "HEAD" else 0))
            if "x-amz-version-id" not in extra:
                self.send_header("x-amz-version-id", "synthetic-v1")
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(result)

        do_PUT = do_POST = do_GET = do_DELETE = do_HEAD = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def factory(
    tmp_path: Path,
    url: str,
    *,
    profile=None,
    admission=None,
    authority=None,
    hook=None,
    attempts=1,
    ddb_url=None,
    dispatcher=None,
):
    profile = profile or runtime_profile()
    admission = admission or SyntheticAdmission()
    created = []

    def base(service, region, timeout):
        client = boto3.session.Session().client(
            service,
            region_name=region,
            endpoint_url=ddb_url if service == "dynamodb" and ddb_url else url,
            aws_access_key_id="synthetic",
            aws_secret_access_key="synthetic",
            config=Config(
                signature_version=UNSIGNED,
                proxies={},
                connect_timeout=timeout,
                read_timeout=timeout,
                retries={"total_max_attempts": attempts, "mode": "standard"},
                request_checksum_calculation="when_required",
                s3={"addressing_style": "path"},
            ),
        )
        if hook:
            hook(client)
        created.append(client)
        return client

    clients = CompetitionAWSClients(
        profile=profile,
        trusted_profile_pin=profile.digest,
        trusted_data_policy_pin="e" * 64,
        observed_account="123456789012",
        observed_role=ROLE,
        current_authority=authority or (lambda: True),
        admission=admission,
        dispatcher=dispatcher
        or SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "dispatch.sqlite")),
        base_factory=base,
        test_endpoints={
            s: (ddb_url if s == "dynamodb" and ddb_url else url)
            for s in ("s3", "dynamodb", "bedrock-runtime", "bedrock", "bedrock-agentcore")
        },
    )
    return clients, admission, created


def test_pending_profile_blocks_before_base_factory(tmp_path: Path) -> None:
    p = CompetitionProfile(profile_id="pending")
    called = []
    with pytest.raises(CompetitionClientFault):
        CompetitionAWSClients(
            profile=p,
            trusted_profile_pin=p.digest,
            trusted_data_policy_pin="e" * 64,
            observed_account="123456789012",
            observed_role=ROLE,
            current_authority=lambda: True,
            admission=SyntheticAdmission(),
            dispatcher=SharedModelDispatcher(
                SqliteModelDispatchStore(tmp_path / "dispatch.sqlite")
            ),
            base_factory=lambda *args, called=called: called.append(args),
        )
    assert called == []


def test_sdk_put_and_exact_version_get_are_admitted_and_sent_same_bytes(tmp_path: Path) -> None:
    with endpoint() as (url, requests):
        clients, admission, _ = factory(tmp_path, url)
        s3 = clients.client("s3", "us-east-1", 5)
        assert (
            s3.put_object(
                Bucket="synthetic-evidence",
                Key="approved/doc.pdf",
                Body=b"synthetic",
                ContentType="application/pdf",
            )["VersionId"]
            == "synthetic-v1"
        )
        response = s3.get_object(
            Bucket="synthetic-evidence", Key="approved/doc.pdf", VersionId="synthetic-v1"
        )
        with response["Body"] as body:
            assert body.read() == b"synthetic-object"
        assert [r["method"] for r in requests] == ["PUT", "GET"]
        assert requests[0]["body"] == b"synthetic"
        assert "versionId=synthetic-v1" in requests[1]["path"]
        assert len(admission.parts) == 2
        assert {p.part_id for p in admission.parts[0]} == {
            "aws.request.body",
            "aws.request.url",
            "aws.request.headers",
            "aws.request.operation",
        }


def test_every_implicit_retry_is_readmitted_and_revocation_stops_second_send(
    tmp_path: Path,
) -> None:
    with endpoint(failures=1) as (url, requests):
        admission = SyntheticAdmission()

        def reject_retry(parts):
            if len(admission.parts) == 2:
                admission.denied = True

        admission.on_check = reject_retry
        clients, _, _ = factory(tmp_path, url, admission=admission, attempts=2)
        with pytest.raises(CompetitionDataFault):
            clients.client("s3", "us-east-1", 5).put_object(
                Bucket="synthetic-evidence", Key="approved/doc.pdf", Body=b"synthetic"
            )
        assert len(requests) == 1
        assert len(admission.parts) == 2


@pytest.mark.parametrize(
    "change", ["method", "path", "query", "duplicate-header", "unknown-header"]
)
def test_prepared_request_mutations_have_zero_physical_sends(tmp_path: Path, change: str) -> None:
    def hook(client):
        def mutate(request, **kwargs):
            if change == "method":
                request.method = "DELETE"
            elif change == "path":
                request.url = request.url.replace("approved/", "unapproved/")
            elif change == "query":
                request.url += "&tagging"
            elif change == "duplicate-header":
                request.headers = {
                    **dict(request.headers.items()),
                    "X-Amz-Meta-Review": b"x",
                    "x-amz-meta-review": b"y",
                }
            else:
                request.headers["X-Unknown"] = b"prohibited"

        client.meta.events.register("before-send.s3.GetObject", mutate)

    with endpoint() as (url, requests):
        clients, _, _ = factory(tmp_path, url, hook=hook)
        denial = {
            "method": "competition_wire_operation_changed",
            "path": "competition_wire_operation_changed",
            "query": "competition_wire_operation_changed",
            "duplicate-header": "competition_duplicate_header",
            "unknown-header": "competition_header_not_supported",
        }[change]
        with pytest.raises((CompetitionClientFault, CompetitionWireFault), match=denial):
            clients.client("s3", "us-east-1", 5).get_object(
                Bucket="synthetic-evidence", Key="approved/doc.pdf", VersionId="synthetic-v1"
            )
        assert requests == []


def test_streams_unknown_actions_prefixes_and_metadata_are_not_exported(tmp_path: Path) -> None:
    with endpoint() as (url, requests):
        clients, admission, _ = factory(tmp_path, url)
        s3 = clients.client("s3", "us-east-1", 5)
        with pytest.raises(CompetitionClientFault):
            s3.put_object(
                Bucket="synthetic-evidence", Key="approved/doc.pdf", Body=io.BytesIO(b"x")
            )
        with pytest.raises(CompetitionClientFault):
            s3.put_object(Bucket="synthetic-evidence", Key="outside/doc.pdf", Body=b"x")
        with pytest.raises(CompetitionClientFault):
            s3.delete_object(Bucket="synthetic-evidence", Key="approved/doc.pdf", VersionId="v1")
        with pytest.raises(CompetitionDataFault):
            s3.put_object(
                Bucket="synthetic-evidence",
                Key="approved/doc.pdf",
                Body=b"safe",
                Metadata={"filename": "prohibited"},
            )
        assert requests == []
        assert len(admission.parts) == 1


def test_current_authority_revocation_after_admission_denies_send(tmp_path: Path) -> None:
    live = [True]
    admission = SyntheticAdmission()
    admission.on_check = lambda parts: live.__setitem__(0, False)
    with endpoint() as (url, requests):
        clients, _, _ = factory(tmp_path, url, admission=admission, authority=lambda: live[0])
        with pytest.raises(CompetitionClientFault, match="revoked"):
            clients.client("s3", "us-east-1", 5).put_object(
                Bucket="synthetic-evidence", Key="approved/doc.pdf", Body=b"safe"
            )
        assert requests == []


def test_slow_authority_cannot_send_after_deadline(tmp_path: Path) -> None:
    with endpoint() as (url, requests):
        clients, _, _ = factory(tmp_path, url)
        s3 = clients.client("s3", "us-east-1", 0.03)

        def slow():
            time.sleep(0.05)
            return True

        with dispatch_authority(slow), pytest.raises(CompetitionClientFault, match="expired"):
            s3.get_object(Bucket="synthetic-evidence", Key="approved/doc.pdf", VersionId="v1")
        assert requests == []


def test_nested_authority_uses_guarded_reads_without_recursion(tmp_path: Path) -> None:
    with endpoint() as (url, requests):
        clients, admission, _ = factory(tmp_path, url)
        ddb = clients.client("dynamodb", "us-east-1", 5)
        checks = []

        def authority():
            checks.append(True)
            ddb.get_item(TableName="jobs", Key={"pk": {"S": "JOB#synthetic"}}, ConsistentRead=True)
            return True

        with dispatch_authority(authority):
            clients.client("s3", "us-east-1", 5).put_object(
                Bucket="synthetic-evidence", Key="approved/doc.pdf", Body=b"safe"
            )
        assert 1 <= len(checks) <= 5
        assert len(requests) == len(checks) + 1
        assert len(admission.parts) == len(requests)


def test_authority_read_scope_does_not_allow_nested_writes(tmp_path: Path) -> None:
    with endpoint() as (url, requests):
        clients, _, _ = factory(tmp_path, url)
        ddb = clients.client("dynamodb", "us-east-1", 5)

        def authority():
            ddb.put_item(TableName="jobs", Item={"pk": {"S": "not-authorized"}})
            return True

        with (
            dispatch_authority(authority),
            pytest.raises(CompetitionClientFault, match="read_scope"),
        ):
            clients.client("s3", "us-east-1", 5).put_object(
                Bucket="synthetic-evidence", Key="approved/doc.pdf", Body=b"safe"
            )
        assert requests == []


def test_composed_publication_and_transfer_use_same_guarded_factory(tmp_path: Path) -> None:
    namespace = uuid4()
    p = runtime_profile()
    data = p.model_dump(mode="json")
    data["resources"][1]["allowed_prefixes"].append(f"sanitized/{namespace}/")
    with endpoint() as (url, requests):
        clients, admission, _ = factory(
            tmp_path, url, profile=CompetitionProfile.model_validate(data)
        )
        publication = build_competition_publication(
            clients,
            bucket_binding="evidence",
            manifest_table_binding="jobs",
            job_table_binding="jobs",
        )
        admission.denied = True
        with pytest.raises(CompetitionDataFault):
            publication.objects.create("approved/result.pdf", b"synthetic")
        assert requests == []
        documents = build_competition_transfer(
            clients,
            bucket_binding="evidence",
            namespace=namespace,
            authorization=SimpleNamespace(),
            verifier=SimpleNamespace(),
            audit=SimpleNamespace(),
        )
        worker = build_competition_worker(
            clients,
            execution=SimpleNamespace(),
            documents=documents,
            principals=SimpleNamespace(),
            job_table_binding="jobs",
            result_bucket_binding="evidence",
        )
        assert worker.service.store.client._owner is clients
        assert worker.service.results.client._owner is clients


def budget_parts(output: int = 8) -> tuple[DataPart, ...]:
    return (
        DataPart(
            "bedrock.request.body",
            "model_prompt",
            json.dumps({"inferenceConfig": {"maxTokens": output}, "messages": []}).encode(),
        ),
    )


@contextmanager
def budget_ledger(*, max_calls: int = 2):
    with mock_aws():
        client = boto3.session.Session().client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="synthetic",
            aws_secret_access_key="synthetic",
        )
        client.create_table(
            TableName="shared",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        p = runtime_profile()
        data = p.model_dump(mode="json")
        data["budget"]["max_calls"] = max_calls
        p = CompetitionProfile.model_validate(data)
        seed = budget_seed_item(p)
        client.put_item(
            TableName="shared", Item=seed, ConditionExpression="attribute_not_exists(pk)"
        )
        yield p, client, seed


def test_two_budget_workers_and_restart_share_atomic_cap() -> None:
    with budget_ledger() as (p, client, seed):

        def spend(_):
            ledger = DynamoDBCompetitionBudget(p, lambda: client)
            try:
                ledger.reserve(
                    operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
                )
                return True
            except CompetitionBudgetFault as error:
                assert str(error) in {
                    "competition_budget_exhausted",
                    "competition_budget_contended",
                }
                return False

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(spend, range(8)))
        assert sum(outcomes) == 2
        assert spend(None) is False
        row = client.get_item(TableName="shared", Key={"pk": seed["pk"]}, ConsistentRead=True)[
            "Item"
        ]
        assert row["calls"] == {"N": "2"}
        assert row["input_tokens"] == {"N": "200"}


def test_unknown_bounds_wire_token_mutation_window_and_ledger_drift_deny() -> None:
    with budget_ledger() as (p, client, seed):
        ledger = DynamoDBCompetitionBudget(p, lambda: client)
        with pytest.raises(CompetitionBudgetFault, match="bounds_unknown"):
            ledger.reserve(
                operation="CountTokens", parameters={"modelId": "example.v1"}, parts=budget_parts()
            )
        with pytest.raises(CompetitionBudgetFault, match="output_unbounded"):
            ledger.reserve(
                operation="Converse",
                parameters={"modelId": "example.v1", "inferenceConfig": {"maxTokens": 8}},
                parts=budget_parts(1000),
            )
        old = DynamoDBCompetitionBudget(
            p, lambda: client, clock=lambda: datetime.now(UTC) + timedelta(days=2)
        )
        with pytest.raises(CompetitionBudgetFault, match="window_expired"):
            old.reserve(
                operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
            )
        client.update_item(
            TableName="shared",
            Key={"pk": seed["pk"]},
            UpdateExpression="SET max_calls = :c",
            ExpressionAttributeValues={":c": {"N": "999"}},
        )
        with pytest.raises(CompetitionBudgetFault, match="ledger_unapproved"):
            ledger.reserve(
                operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
            )


def test_unknown_reservation_result_is_not_refunded() -> None:
    with budget_ledger(max_calls=1) as (p, client, _seed):

        class LostResponse:
            get_item = client.get_item

            def update_item(self, **kwargs):
                client.update_item(**kwargs)
                raise TimeoutError("synthetic dropped response")

        ledger = DynamoDBCompetitionBudget(p, lambda: LostResponse())
        with pytest.raises(CompetitionBudgetFault, match="reservation_unknown"):
            ledger.reserve(
                operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
            )
        with pytest.raises(CompetitionBudgetFault, match="exhausted"):
            DynamoDBCompetitionBudget(p, lambda: client).reserve(
                operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
            )


def test_production_rejects_local_or_mismatched_central_dispatch(tmp_path):
    p = runtime_profile()
    for dispatcher in (
        SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "local.sqlite")),
        SharedModelDispatcher(DynamoDBModelDispatchStore(None, table_name="other")),
    ):
        called = []
        with pytest.raises(CompetitionClientFault, match="central_dispatch"):
            CompetitionAWSClients(
                profile=p,
                trusted_profile_pin=p.digest,
                trusted_data_policy_pin="e" * 64,
                observed_account="123456789012",
                observed_role=ROLE,
                current_authority=lambda: True,
                admission=SyntheticAdmission(),
                dispatcher=dispatcher,
                base_factory=lambda *args, called=called: called.append(args),
            )
        assert called == []


def test_budget_rechecks_all_caps_atomically_between_read_and_write():
    with budget_ledger() as (p, client, seed):

        class Drift:
            get_item = client.get_item
            changed = False

            def update_item(self, **kwargs):
                if not self.changed:
                    self.changed = True
                    client.update_item(
                        TableName="shared",
                        Key={"pk": seed["pk"]},
                        UpdateExpression="SET max_calls = :c",
                        ExpressionAttributeValues={":c": {"N": "999"}},
                    )
                return client.update_item(**kwargs)

        proxy = Drift()
        with pytest.raises(CompetitionBudgetFault, match="ledger_unapproved"):
            DynamoDBCompetitionBudget(p, lambda: proxy).reserve(
                operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
            )
        assert client.get_item(TableName="shared", Key={"pk": seed["pk"]})["Item"]["calls"] == {
            "N": "0"
        }


def test_real_model_retries_and_restart_charge_same_guarded_central_ledger(tmp_path):
    with (
        budget_ledger() as (p, raw, seed),
        endpoint(ddb=raw) as (ddb_url, coordination),
        endpoint(failures=10) as (url, requests),
    ):

        def construct():
            return factory(
                tmp_path,
                url,
                profile=p,
                ddb_url=ddb_url,
                attempts=3,
                dispatcher=SharedModelDispatcher(
                    DynamoDBModelDispatchStore(raw, table_name="shared")
                ),
            )[0]

        clients = construct()
        assert clients.dispatcher.store.client is not raw
        for attempt in range(2):
            if attempt:
                clients = construct()
            with (
                dispatch_guard(DispatchGuard(deadline=time.monotonic() + 30, max_sends=10)),
                pytest.raises(CompetitionBudgetFault, match="exhausted"),
            ):
                clients.client("bedrock-runtime", "us-east-1", 20).converse(
                    modelId="example.v1",
                    messages=[{"role": "user", "content": [{"text": "synthetic"}]}],
                    inferenceConfig={"maxTokens": 8},
                )
            assert len(requests) == 2
        row = raw.get_item(TableName="shared", Key={"pk": seed["pk"]})["Item"]
        assert row["calls"] == {"N": "2"}
        assert row["output_tokens"] == {"N": "20"}
        assert coordination and all(
            r["headers"]
            .get("X-Amz-Target", r["headers"].get("x-amz-target", ""))
            .startswith("DynamoDB_")
            for r in coordination
        )


def _process_budget_spend(arguments):
    profile_json, url = arguments
    profile = CompetitionProfile.model_validate_json(profile_json)
    client = boto3.session.Session().client(
        "dynamodb",
        region_name="us-east-1",
        endpoint_url=url,
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
        config=Config(proxies={}, retries={"total_max_attempts": 1}),
    )
    try:
        DynamoDBCompetitionBudget(profile, lambda: client).reserve(
            operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
        )
        return True
    except CompetitionBudgetFault as error:
        assert str(error) in {"competition_budget_exhausted", "competition_budget_contended"}
        return False


def test_separate_processes_share_one_moto_dynamodb_budget():
    with budget_ledger() as (p, raw, seed), endpoint(ddb=raw) as (url, requests):
        with multiprocessing.get_context("spawn").Pool(2) as pool:
            outcomes = pool.map(_process_budget_spend, [(p.model_dump_json(), url)] * 6)
        assert sum(outcomes) == 2
        assert _process_budget_spend((p.model_dump_json(), url)) is False
        assert raw.get_item(TableName="shared", Key={"pk": seed["pk"]})["Item"]["calls"] == {
            "N": "2"
        }
        assert requests


def test_actual_async_snapshot_authority_reads_guarded_s3_without_recursion(tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = runpy.run_path(str(root / "cloud_tests/extraction_smoke.py"))["synthetic_source"]
    local, principal, pages = source(tmp_path)
    run = pages[0].run
    namespace = uuid4()
    prefix = f"sanitized/{namespace}/"
    with sqlite3.connect(local.storage.database) as connection:
        objects = {
            f"/synthetic-evidence/{prefix}{key}": (version, content)
            for key, version, content in connection.execute(
                "SELECT key, version, content FROM documents"
            )
        }
    p = runtime_profile()
    data = p.model_dump(mode="json")
    data["resources"][1]["allowed_prefixes"].append(prefix)
    with endpoint(objects=objects) as (url, requests):
        clients, _, _ = factory(tmp_path, url, profile=CompetitionProfile.model_validate(data))
        documents = DocumentTransferService(
            S3DocumentStorage(
                clients.client("s3", "us-east-1", 20),
                S3DocumentConfiguration("synthetic-evidence", "123456789012", namespace),
            ),
            local.authorization,
            local.verifier,
            local.audit,
        )
        record = SimpleNamespace(
            principal_id=principal.actor.actor_id, case_id=run.revision.case_id, current_run=run
        )
        submission = SimpleNamespace(revision=run.revision, documents=(pages[0].source.document,))

        async def read_submission(**kwargs):
            await asyncio.to_thread(
                clients.client("dynamodb", "us-east-1", 5).get_item,
                TableName="jobs",
                Key={"pk": {"S": "synthetic-run"}},
                ConsistentRead=True,
            )
            return submission

        bound = SnapshotBoundExecution(
            SimpleNamespace(),
            documents,
            SimpleNamespace(read_submission=read_submission),
            AsyncMock(read=AsyncMock(return_value=principal)),
        )

        async def exercise():
            await bound._require(record)
            with dispatch_async_authority(lambda: bound._require(record)):
                await asyncio.wait_for(
                    asyncio.to_thread(
                        clients.client("s3", "us-east-1", 20).put_object,
                        Bucket="synthetic-evidence",
                        Key="approved/out.pdf",
                        Body=b"synthetic",
                    ),
                    timeout=10,
                )

        asyncio.run(exercise())
        puts = [r for r in requests if r["method"] == "PUT"]
        assert len(puts) == 1
        assert puts[0]["body"] == b"synthetic"
        assert len(requests) < 100
        assert all(
            "versionId=" in r["path"]
            for r in requests
            if r["method"] == "GET" and "/sanitized/" in r["path"]
        )


@pytest.mark.parametrize("change", ["missing-context", "low-input", "missing-price", "low-cost"])
def test_unverified_or_understated_worst_case_reservation_denies(change):
    with budget_ledger() as (p, client, _seed):
        data = p.model_dump(mode="json")
        reservation = data["budget"]["operation_reservations"][0]
        if change == "missing-context":
            data["models"][0]["context_window_tokens"] = None
        elif change == "low-input":
            reservation["input_tokens"] = 1
        elif change == "missing-price":
            reservation["input_price_per_million_usd"] = None
        else:
            reservation["cost_usd"] = "0.000001"
        changed = CompetitionProfile.model_validate(data)
        with pytest.raises(CompetitionBudgetFault, match="conservative_basis_missing"):
            DynamoDBCompetitionBudget(changed, lambda: client).reserve(
                operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
            )


def test_window_expiry_during_successful_cas_keeps_reservation_consumed():
    with budget_ledger() as (p, client, seed):
        now = [datetime.now(UTC)]

        class DelayedWrite:
            get_item = client.get_item

            def update_item(self, **kwargs):
                result = client.update_item(**kwargs)
                now[0] += timedelta(days=2)
                return result

        with pytest.raises(CompetitionBudgetFault, match="window_expired"):
            DynamoDBCompetitionBudget(p, lambda: DelayedWrite(), clock=lambda: now[0]).reserve(
                operation="Converse", parameters={"modelId": "example.v1"}, parts=budget_parts()
            )
        assert client.get_item(TableName="shared", Key={"pk": seed["pk"]})["Item"]["calls"] == {
            "N": "1"
        }
