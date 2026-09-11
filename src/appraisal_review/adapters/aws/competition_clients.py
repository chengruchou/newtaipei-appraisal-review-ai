"""Explicit competition SDK boundary: exact authority, scope and every physical send."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit

from appraisal_review.adapters.aws.bedrock_dispatch import install_bedrock_dispatch
from appraisal_review.adapters.aws.competition_budget import DynamoDBCompetitionBudget
from appraisal_review.adapters.aws.competition_wire import WireBinding, install_wire_binding
from appraisal_review.adapters.aws.dynamodb_model_dispatch import DynamoDBModelDispatchStore
from appraisal_review.adapters.local.sqlite_model_dispatch import SqliteModelDispatchStore
from appraisal_review.application.competition_preflight import (
    check_model_destinations,
    check_profile,
)
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.domain.competition_data import DataPart, DataSurface
from appraisal_review.domain.competition_profile import CompetitionProfile, ResourceBinding
from appraisal_review.ports.competition_data import CompetitionDataAdmission
from appraisal_review.ports.model_dispatch import inherited_dispatch_authority


class CompetitionClientFault(RuntimeError):
    """Value-free denial before transport; never include SDK/source content."""


_S3_ACTIONS = {
    "PutObject": "s3:PutObject",
    "GetBucketVersioning": "s3:GetBucketVersioning",
    "GetBucketEncryption": "s3:GetEncryptionConfiguration",
    "GetPublicAccessBlock": "s3:GetBucketPublicAccessBlock",
    "GetBucketOwnershipControls": "s3:GetBucketOwnershipControls",
    "GetBucketPolicyStatus": "s3:GetBucketPolicyStatus",
}
_DDB_ACTIONS = {
    "GetItem": "dynamodb:GetItem",
    "PutItem": "dynamodb:PutItem",
    "UpdateItem": "dynamodb:UpdateItem",
    "DeleteItem": "dynamodb:DeleteItem",
    "Query": "dynamodb:Query",
    "ConditionCheck": "dynamodb:ConditionCheckItem",
}
_MODEL_ACTIONS = {
    "Converse": "bedrock:InvokeModel",
    "InvokeModel": "bedrock:InvokeModel",
    "CountTokens": "bedrock:CountTokens",
    "GetFoundationModel": "bedrock:GetFoundationModel",
    "GetInferenceProfile": "bedrock:GetInferenceProfile",
}
_AUTH_HEADERS = frozenset({"authorization", "x-amz-date", "date", "x-amz-security-token"})
_METADATA_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "content-md5",
        "content-encoding",
        "host",
        "user-agent",
        "x-amz-target",
        "x-amz-content-sha256",
        "x-amz-checksum-sha256",
        "x-amz-checksum-crc32",
        "x-amz-sdk-checksum-algorithm",
        "x-amz-checksum-mode",
        "x-amz-expected-bucket-owner",
        "x-amz-server-side-encryption",
        "x-amz-tagging",
        "if-none-match",
        "if-match",
        "accept",
        "expect",
        "amz-sdk-invocation-id",
        "amz-sdk-request",
    }
)


@dataclass(frozen=True)
class _ModelRouting:
    model_id: str
    kind: str
    profile_digest: str
    snapshot_digest: str
    approved_arns: tuple[str, ...]
    deadline: float
    generation: int
    discovered_arns: tuple[str, ...] | None = None


@dataclass(frozen=True)
class _Call:
    service: str
    operation: str
    parameters: dict[str, Any]
    deadline: float
    authority: Callable[[], bool]
    expected_method: str
    expected_path: str
    expected_query: dict[str, str]
    region: str
    routing: _ModelRouting | None


_CALL: ContextVar[_Call | None] = ContextVar("competition_sdk_call", default=None)
_AUTHORITY_READ: ContextVar[bool] = ContextVar("competition_authority_read", default=False)


def _check_authority(callback: Callable[[], bool]) -> bool:
    token = _AUTHORITY_READ.set(True)
    try:
        return callback() is True
    finally:
        _AUTHORITY_READ.reset(token)


def _snapshot(value: Any) -> Any:
    if value is None or type(value) in (str, int, float, bool, bytes):
        return value
    if type(value) is dict and all(type(k) is str for k in value):
        return {k: _snapshot(v) for k, v in value.items()}
    if type(value) in (list, tuple):
        return [_snapshot(v) for v in value]
    raise CompetitionClientFault("competition_immutable_parameters_required")


class _Admission:
    def __init__(self, owner: CompetitionAWSClients) -> None:
        self.owner = owner

    def check(self, parts: tuple[DataPart, ...]) -> None:
        self.owner.require_current()
        call = self.owner.require_call()
        url = next((p.content.decode() for p in parts if p.part_id.endswith(".url")), None)
        if url is None:
            raise CompetitionClientFault("competition_wire_url_required")
        self.owner.validate_wire_url(url, call)
        self.owner.admission.check(parts)
        if call.service in {"bedrock", "bedrock-runtime"}:
            self.owner.budget.reserve(
                operation=call.operation, parameters=call.parameters, parts=parts
            )
        self.owner.require_current()
        self.owner.require_call()


class _Transport:
    def __init__(self, transport: Any, owner: CompetitionAWSClients, binding: WireBinding) -> None:
        self.transport, self.owner, self.binding = transport, owner, binding

    def send(self, request: Any) -> Any:
        call = self.owner.require_call()
        self.owner.require_current()
        body = b"" if request.body is None else request.body
        # The SDK's STS query serializer produces immutable form text. Freeze
        # its exact UTF-8 bytes before admission and transport, as for JSON APIs.
        if call.service == "sts" and type(body) is str:
            body = body.encode("utf-8")
        if type(body) is not bytes or type(request.url) is not str:
            raise CompetitionClientFault("competition_immutable_wire_required")
        prepared = copy(request)
        prepared.body, prepared.url = body, request.url
        operation_bytes = self.binding.check(prepared)
        prepared.headers = MappingProxyType(dict(request.headers.items()))
        reviewed_headers: dict[str, str] = {}
        header_names: set[str] = set()
        for key, value in prepared.headers.items():
            if type(key) is not str or type(value) not in (str, bytes):
                raise CompetitionClientFault("competition_header_schema")
            name = key.lower()
            if name in header_names:
                raise CompetitionClientFault("competition_duplicate_header")
            header_names.add(name)
            if name in _AUTH_HEADERS:
                continue
            if name not in _METADATA_HEADERS and not name.startswith("x-amz-meta-"):
                raise CompetitionClientFault("competition_header_not_supported")
            reviewed_headers[name] = value.decode("latin1") if type(value) is bytes else value
        self.owner.validate_wire_url(prepared.url, call)
        if call.service in {"dynamodb", "sqs"}:
            try:
                actual = json.loads(body)
            except (ValueError, UnicodeError):
                raise CompetitionClientFault("competition_wire_schema") from None
            if not isinstance(actual, dict):
                raise CompetitionClientFault("competition_wire_schema")
            self.owner.authorize(call.service, call.operation, actual)
            target = reviewed_headers.get("x-amz-target", "")
            if target.split(".")[-1] != call.operation:
                raise CompetitionClientFault("competition_wire_operation_changed")
        if call.service == "sts" and parse_qs(body.decode()).get("Action") != [call.operation]:
            raise CompetitionClientFault("competition_wire_operation_changed")
        content_type = reviewed_headers.get("content-type", "")
        surface: DataSurface = "pdf" if content_type == "application/pdf" else "extraction_json"
        parts = (
            DataPart("aws.request.operation", "metadata", operation_bytes),
            DataPart("aws.request.body", surface, body),
            DataPart("aws.request.url", "metadata", prepared.url.encode()),
            DataPart(
                "aws.request.headers",
                "metadata",
                json.dumps(reviewed_headers, sort_keys=True, separators=(",", ":")).encode(),
            ),
        )
        self.owner._wire_admission.check(parts)
        return self.transport.send(prepared)

    def close(self) -> None:
        self.transport.close()


class _Client:
    def __init__(
        self,
        client: Any,
        owner: CompetitionAWSClients,
        service: str,
        timeout: float,
        routing: _ModelRouting | None = None,
    ) -> None:
        self._client, self._owner, self._service, self._timeout = client, owner, service, timeout
        self._routing = routing

    @property
    def meta(self) -> Any:
        return self._client.meta

    @property
    def exceptions(self) -> Any:
        return self._client.exceptions

    def __getattr__(self, name: str) -> Any:
        operation = self._client.meta.method_to_api_mapping.get(name)
        if operation is None or name.startswith("_"):
            raise CompetitionClientFault("competition_operation_not_supported")

        def invoke(**kwargs: Any) -> Any:
            authority_read = _AUTHORITY_READ.get()
            if authority_read:
                read_allowed = (
                    self._service == "dynamodb" and operation in {"GetItem", "TransactGetItems"}
                ) or (
                    self._service == "s3"
                    and (
                        operation == "HeadObject"
                        or (
                            operation == "GetObject"
                            and kwargs.get("VersionId") not in (None, "", "null")
                        )
                        or operation in set(_S3_ACTIONS) - {"PutObject"}
                    )
                )
                if not read_allowed:
                    raise CompetitionClientFault("competition_authority_read_scope")
            self._owner.require_current()
            parameters: dict[str, Any] = _snapshot(kwargs)
            self._owner.authorize(
                self._service,
                operation,
                parameters,
                region=self.meta.region_name,
                routing=self._routing,
            )
            if self._service == "s3":
                parameters["ExpectedBucketOwner"] = self._owner.profile.account_id
            authority = (lambda: True) if authority_read else inherited_dispatch_authority()
            model = self._client.meta.service_model.operation_model(operation)
            request_uri = model.http["requestUri"]
            query = dict(parse_qsl(urlsplit(request_uri).query, keep_blank_values=True))
            path = urlsplit(request_uri).path
            for parameter, shape in model.input_shape.members.items():
                location = shape.serialization.get("location")
                label = shape.serialization.get("name", parameter)
                if location == "uri" and parameter in parameters:
                    path = path.replace("{" + label + "}", str(parameters[parameter]))
                    path = path.replace("{" + label + "+}", str(parameters[parameter]))
                if location == "querystring" and parameter in parameters:
                    query[label] = str(parameters[parameter])
            if re.search(r"\{[^}]+\}", path):
                raise CompetitionClientFault("competition_request_uri_unresolved")
            call = _Call(
                self._service,
                operation,
                _snapshot(parameters),
                time.monotonic() + self._timeout,
                authority,
                model.http["method"],
                path,
                query,
                self.meta.region_name,
                self._routing,
            )
            token = _CALL.set(call)
            try:
                return getattr(self._client, name)(**parameters)
            finally:
                _CALL.reset(token)

        return invoke


class CompetitionModelClients:
    """One preflight's immutable model scope, never a shared discovery cache."""

    def __init__(
        self, owner: CompetitionAWSClients, model_id: str, kind: str, deadline: float
    ) -> None:
        owner.require_current()
        matches = [m for m in owner.profile.models if m.model_id == model_id]
        if not matches or any(m.kind != kind for m in matches):
            raise CompetitionClientFault("competition_model_not_approved")
        first = matches[0]
        arns = tuple(d.arn for d in first.destinations)
        if (
            first.destination_snapshot_sha256 is None
            or any(
                m.destination_snapshot_sha256 != first.destination_snapshot_sha256 for m in matches
            )
            or not check_model_destinations(owner.profile, model_id, arns)
        ):
            raise CompetitionClientFault("competition_model_routing_ambiguous")
        self.owner = owner
        self._invalidated = False
        self._routing = _ModelRouting(
            model_id,
            kind,
            owner.profile.digest,
            first.destination_snapshot_sha256,
            arns,
            deadline,
            owner._routing_generations.get(model_id, 0),
        )

    def invalidate(self) -> None:
        if not self._invalidated:
            model_id = self._routing.model_id
            self.owner._routing_generations[model_id] = (
                self.owner._routing_generations.get(model_id, 0) + 1
            )
            self._invalidated = True

    def validate_destinations(self, discovered_arns: tuple[str, ...]) -> None:
        self.owner.require_current()
        if not check_model_destinations(
            self.owner.profile, self._routing.model_id, discovered_arns
        ):
            raise CompetitionClientFault("competition_model_routing_changed")
        self._routing = replace(self._routing, discovered_arns=discovered_arns)

    def client(self, service: str, region: str, timeout: float) -> Any:
        if service == "bedrock-runtime" and self._routing.discovered_arns is None:
            raise CompetitionClientFault("competition_model_routing_unverified")
        client = self.owner.client(service, region, timeout)
        if service not in {"bedrock", "bedrock-runtime"}:
            return client
        return _Client(client._client, self.owner, service, timeout, self._routing)


class CompetitionAWSClients:
    """Trusted explicit composition. Construct no base client before all gates pass.

    Base factories return fresh low-level botocore clients; only URLLib3 transport
    is supported. No resource API, custom production endpoint or presigner seam.
    Test endpoints must be explicitly pinned loopback origins. This constructor
    never chooses a profile, creates a default session or reads environment pins.
    """

    def __init__(
        self,
        *,
        profile: CompetitionProfile,
        trusted_profile_pin: str,
        trusted_data_policy_pin: str,
        observed_account: str,
        observed_role: str,
        current_authority: Callable[[], bool],
        admission: CompetitionDataAdmission,
        dispatcher: SharedModelDispatcher,
        base_factory: Callable[[str, str, float], Any],
        test_endpoints: dict[str, str] | None = None,
    ) -> None:
        self.profile = CompetitionProfile.model_validate_json(profile.model_dump_json())
        self.trusted_profile_pin, self.trusted_data_policy_pin = (
            trusted_profile_pin,
            trusted_data_policy_pin,
        )
        self.observed_account, self.observed_role = observed_account, observed_role
        self.current_authority, self.admission, self.dispatcher = (
            current_authority,
            admission,
            dispatcher,
        )
        self._base_factory = base_factory
        self._test_endpoints = dict(test_endpoints or {})
        self._clients: dict[tuple[str, str, float], _Client] = {}
        self._routing_generations: dict[str, int] = {}
        self._wire_admission = _Admission(self)
        for endpoint in self._test_endpoints.values():
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or not parsed.port
                or (
                    parsed.username
                    or parsed.password
                    or parsed.path not in ("", "/")
                    or parsed.query
                    or parsed.fragment
                )
            ):
                raise CompetitionClientFault("competition_test_endpoint_invalid")
        self._validate_dispatch_store(dispatcher)
        self.require_current()
        if type(dispatcher.store) is DynamoDBModelDispatchStore:
            # Rebuild the coordinator from this factory; never retain its raw client.
            self.dispatcher = SharedModelDispatcher(
                DynamoDBModelDispatchStore(
                    _Coordinator(self), table_name=dispatcher.store.table_name
                ),
                scope=dispatcher.scope,
                interval_seconds=dispatcher.interval_seconds,
            )
        self._bound_dispatch_store = self.dispatcher.store
        self.budget = DynamoDBCompetitionBudget(
            self.profile, lambda: self.client("dynamodb", self.profile.primary_region, 10)
        )

    def _validate_dispatch_store(self, dispatcher: SharedModelDispatcher) -> None:
        if type(dispatcher) is not SharedModelDispatcher:
            raise CompetitionClientFault("competition_central_dispatch_required")
        arn = self.profile.throttle.central_store_arn
        store = dispatcher.store
        if type(store) is DynamoDBModelDispatchStore:
            if not arn or arn.split(":table/", 1)[-1] != store.table_name:
                raise CompetitionClientFault("competition_central_dispatch_mismatch")
            if not any(r.kind == "table" and r.arn == arn for r in self.profile.resources):
                raise CompetitionClientFault("competition_central_dispatch_mismatch")
            for action in ("dynamodb:GetItem", "dynamodb:UpdateItem"):
                self._action(action)
        elif not (
            type(store) is SqliteModelDispatchStore
            and {"dynamodb", "bedrock", "bedrock-runtime"} <= self._test_endpoints.keys()
        ):
            raise CompetitionClientFault("competition_central_dispatch_required")

    def require_current(self) -> None:
        budget = getattr(self, "budget", None)
        if budget is not None and budget.profile.digest != self.profile.digest:
            raise CompetitionClientFault("competition_budget_profile_changed")
        bound = getattr(self, "_bound_dispatch_store", None)
        if bound is not None and self.dispatcher.store is not bound:
            raise CompetitionClientFault("competition_central_dispatch_changed")
        self._validate_dispatch_store(self.dispatcher)
        if (
            self.admission is None
            or self.dispatcher is None
            or (not _AUTHORITY_READ.get() and not _check_authority(self.current_authority))
        ):
            raise CompetitionClientFault("competition_authority_revoked")
        if self.dispatcher.scope != self.profile.throttle.scope or (
            self.dispatcher.interval_seconds < self.profile.throttle.interval_seconds
        ):
            raise CompetitionClientFault("competition_dispatch_scope_mismatch")
        findings = check_profile(
            self.profile,
            trusted_profile_digest=self.trusted_profile_pin,
            trusted_data_policy_digest=self.trusted_data_policy_pin,
            observed_account_id=self.observed_account,
            observed_role_arn=self.observed_role,
        )
        if findings:
            raise CompetitionClientFault("competition_profile_not_approved")

    def require_call(self) -> _Call:
        call = _CALL.get()
        if (
            call is None
            or time.monotonic() >= call.deadline
            or not _check_authority(call.authority)
            or (time.monotonic() >= call.deadline)
        ):
            raise CompetitionClientFault("competition_call_authority_expired")
        if call.service in {"bedrock", "bedrock-runtime"}:
            self.authorize(
                call.service,
                call.operation,
                call.parameters,
                region=call.region,
                routing=call.routing,
            )
        return call

    def for_model(self, model_id: str, kind: str, deadline: float) -> CompetitionModelClients:
        return CompetitionModelClients(self, model_id, kind, deadline)

    def _authorize_model(
        self,
        operation: str,
        identifier: Any,
        region: str,
        routing: _ModelRouting | None,
    ) -> None:
        if routing is None:
            # A foundation model has one static destination. Dynamic profiles
            # require fresh discovery through their own bound preflight client.
            arn = (
                identifier
                if isinstance(identifier, str) and identifier.startswith("arn:")
                else (f"arn:aws:bedrock:{region}::foundation-model/{identifier}")
            )
            if operation == "GetInferenceProfile" or not any(
                m.kind == "foundation"
                and identifier
                in (m.model_id, m.destinations[0].arn, m.destinations[0].arn.split("/", 1)[1])
                and arn in {d.arn for d in m.destinations}
                and arn.split(":")[3] == region
                for m in self.profile.models
                if m.destinations
            ):
                raise CompetitionClientFault("competition_model_routing_unverified")
            return
        matches = [m for m in self.profile.models if m.model_id == routing.model_id]
        if (
            time.monotonic() >= routing.deadline
            or routing.generation != self._routing_generations.get(routing.model_id, 0)
            or routing.profile_digest != self.profile.digest
            or not matches
            or any(
                m.kind != routing.kind or m.destination_snapshot_sha256 != routing.snapshot_digest
                for m in matches
            )
            or not check_model_destinations(self.profile, routing.model_id, routing.approved_arns)
        ):
            raise CompetitionClientFault("competition_model_routing_expired")
        if operation == "GetInferenceProfile":
            if identifier != routing.model_id or routing.kind == "foundation":
                raise CompetitionClientFault("competition_model_not_approved")
            return
        if routing.discovered_arns is None or not check_model_destinations(
            self.profile, routing.model_id, routing.discovered_arns
        ):
            raise CompetitionClientFault("competition_model_routing_unverified")
        if operation == "GetFoundationModel":
            arn = (
                identifier
                if isinstance(identifier, str) and identifier.startswith("arn:")
                else (f"arn:aws:bedrock:{region}::foundation-model/{identifier}")
            )
            if arn not in routing.discovered_arns or arn.split(":")[3] != region:
                raise CompetitionClientFault("competition_model_not_approved")
        elif identifier != routing.model_id or region != self.profile.primary_region:
            raise CompetitionClientFault("competition_model_not_approved")

    def binding(self, name: str, kind: str) -> ResourceBinding:
        self.require_current()
        matches = [r for r in self.profile.resources if r.name == name and r.kind == kind and r.arn]
        if len(matches) != 1:
            raise CompetitionClientFault("competition_resource_not_bound")
        return matches[0]

    def _action(self, action: str) -> None:
        if action not in self.profile.iam_actions:
            raise CompetitionClientFault("competition_iam_action_not_approved")

    def _table(self, params: dict[str, Any]) -> None:
        table = params.get("TableName")
        matches = [
            r
            for r in self.profile.resources
            if r.kind == "table" and r.arn and table in (r.arn, r.arn.split("table/", 1)[-1])
        ]
        if len(matches) != 1 or (
            "IndexName" in params and params["IndexName"] not in matches[0].allowed_indexes
        ):
            raise CompetitionClientFault("competition_table_not_approved")

    def authorize(
        self,
        service: str,
        operation: str,
        params: dict[str, Any],
        *,
        region: str = "",
        routing: _ModelRouting | None = None,
    ) -> None:
        if service == "s3":
            bucket, key = params.get("Bucket"), params.get("Key")
            matches = [
                r
                for r in self.profile.resources
                if r.kind == "bucket" and r.arn == f"arn:aws:s3:::{bucket}"
            ]
            if (
                len(matches) != 1
                or params.get("ExpectedBucketOwner", self.profile.account_id)
                != self.profile.account_id
            ):
                raise CompetitionClientFault("competition_bucket_not_approved")
            if key is not None and (
                type(key) is not str
                or any(part in (".", "..", "") for part in key.split("/"))
                or not any(key.startswith(prefix) for prefix in matches[0].allowed_prefixes)
            ):
                raise CompetitionClientFault("competition_object_prefix_not_approved")
            if operation in {"HeadObject", "GetObject"}:
                if "VersionId" in params and (
                    type(params["VersionId"]) is not str or params["VersionId"] in ("", "null")
                ):
                    raise CompetitionClientFault("competition_object_version_invalid")
                action = "s3:GetObjectVersion" if "VersionId" in params else "s3:GetObject"
            else:
                action = _S3_ACTIONS.get(operation, "")
            self._action(action)
            if operation == "PutObject" and (
                type(params.get("Body")) is not bytes
                or any(
                    k in params
                    for k in ("ACL", "GrantRead", "GrantFullControl", "WebsiteRedirectLocation")
                )
            ):
                raise CompetitionClientFault("competition_immutable_private_upload_required")
            return
        if service == "dynamodb":
            if operation in {"TransactGetItems", "TransactWriteItems"}:
                entries = params.get("TransactItems")
                if not isinstance(entries, list) or not entries:
                    raise CompetitionClientFault("competition_transaction_schema")
                for entry in entries:
                    if not isinstance(entry, dict) or len(entry) != 1:
                        raise CompetitionClientFault("competition_transaction_schema")
                    kind, body = next(iter(entry.items()))
                    mapped = {
                        "Get": "GetItem",
                        "Put": "PutItem",
                        "Update": "UpdateItem",
                        "Delete": "DeleteItem",
                        "ConditionCheck": "ConditionCheck",
                    }.get(kind)
                    if (
                        mapped is None
                        or not isinstance(body, dict)
                        or (operation == "TransactGetItems" and mapped != "GetItem")
                    ):
                        raise CompetitionClientFault("competition_transaction_schema")
                    self.authorize(service, mapped, body)
            else:
                self._action(_DDB_ACTIONS.get(operation, ""))
                self._table(params)
            return
        if service in {"bedrock", "bedrock-runtime"}:
            self._action(_MODEL_ACTIONS.get(operation, ""))
            identifier = params.get(
                "modelId", params.get("modelIdentifier", params.get("inferenceProfileIdentifier"))
            )
            self._authorize_model(operation, identifier, region, routing)
            return
        if service == "sts" and operation == "GetCallerIdentity":
            self._action("sts:GetCallerIdentity")
            return
        if service == "bedrock-agentcore" and operation == "InvokeAgentRuntime":
            self._action("bedrock-agentcore:InvokeAgentRuntime")
            if not any(
                r.kind == "runtime" and r.arn == params.get("agentRuntimeArn")
                for r in self.profile.resources
            ):
                raise CompetitionClientFault("competition_runtime_not_approved")
            if params.get("qualifier", "DEFAULT") != "DEFAULT":
                raise CompetitionClientFault("competition_runtime_qualifier_not_approved")
            return
        raise CompetitionClientFault("competition_operation_not_supported")

    def validate_wire_url(self, url: str, call: _Call) -> None:
        parsed = urlsplit(url)
        expected = self._test_endpoints.get(call.service)
        if expected:
            origin = urlsplit(expected)
            valid = (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc)
        else:
            region = self.profile.primary_region
            allowed = {f"{call.service}.{region}.amazonaws.com"}
            if call.service in {"bedrock", "bedrock-runtime"}:
                allowed |= {
                    f"{call.service}.{d.region}.amazonaws.com"
                    for m in self.profile.models
                    for d in m.destinations
                }
            if call.service == "s3":
                allowed.add(f"{call.parameters.get('Bucket')}.s3.{region}.amazonaws.com")
            valid = (
                parsed.scheme == "https"
                and parsed.hostname in allowed
                and parsed.port in (None, 443)
            )
        if not valid or parsed.username or parsed.password or parsed.fragment:
            raise CompetitionClientFault("competition_endpoint_not_approved")
        if call.service == "s3":
            bucket = call.parameters["Bucket"]
            virtual = parsed.hostname == f"{bucket}.s3.{self.profile.primary_region}.amazonaws.com"
            expected_path = "/" if virtual else f"/{bucket}"
            if "Key" in call.parameters:
                expected_path = ("/" if virtual else f"/{bucket}/") + call.parameters["Key"]
            if unquote(parsed.path) != expected_path:
                raise CompetitionClientFault("competition_wire_resource_changed")
            if parse_qs(parsed.query).get("versionId") != (
                [call.parameters["VersionId"]] if "VersionId" in call.parameters else None
            ):
                raise CompetitionClientFault("competition_wire_version_changed")
        elif unquote(parsed.path or "/") != call.expected_path:
            raise CompetitionClientFault("competition_wire_resource_changed")
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if len(query) != len(dict(query)):
            raise CompetitionClientFault("competition_wire_query_changed")
        actual_query = dict(query)
        if call.service == "s3" and actual_query.get("x-id") == call.operation:
            actual_query.pop("x-id")
        if actual_query != call.expected_query:
            raise CompetitionClientFault("competition_wire_query_changed")

    def client(self, service: str, region: str, timeout: float) -> Any:
        self.require_current()
        if service not in {
            "s3",
            "dynamodb",
            "sts",
            "bedrock",
            "bedrock-runtime",
            "bedrock-agentcore",
        }:
            raise CompetitionClientFault("competition_service_not_supported")
        allowed_regions: set[str] = {self.profile.primary_region}
        if service in {"bedrock", "bedrock-runtime"} and self.profile.allow_cross_region:
            allowed_regions |= {d.region for m in self.profile.models for d in m.destinations}
        if region not in allowed_regions or not 0 < timeout <= 600:
            raise CompetitionClientFault("competition_client_scope")
        key = (service, region, timeout)
        if key in self._clients:
            return self._clients[key]
        client = self._base_factory(service, region, timeout)
        from botocore.client import BaseClient
        from botocore.handlers import convert_body_to_file_like_object
        from botocore.httpsession import URLLib3Session

        if (
            not isinstance(client, BaseClient)
            or client.meta.service_model.service_name != service
            or client.meta.region_name != region
        ):
            raise CompetitionClientFault("competition_sdk_client_required")
        native: Any = client
        transport: Any = native._endpoint.http_session
        endpoint = self._test_endpoints.get(service, f"https://{service}.{region}.amazonaws.com")
        if (
            client.meta.endpoint_url.rstrip("/") != endpoint.rstrip("/")
            or type(transport) is not URLLib3Session
        ):
            raise CompetitionClientFault("competition_transport_not_supported")
        checked_transport: Any = transport
        if checked_transport._proxy_config._proxies or (
            service not in self._test_endpoints and checked_transport._verify is False
        ):
            raise CompetitionClientFault("competition_transport_not_supported")
        if service in {"bedrock", "bedrock-runtime"}:
            install_bedrock_dispatch(
                client,
                self.dispatcher,
                competition_admission=self._wire_admission,
                allow_loopback_for_testing=service in self._test_endpoints,
            )
        else:
            if service == "s3":
                client.meta.events.unregister(
                    "before-parameter-build.s3.PutObject", convert_body_to_file_like_object
                )
            native._endpoint.http_session = _Transport(
                transport, self, install_wire_binding(client)
            )
        guarded = _Client(client, self, service, timeout)
        self._clients[key] = guarded
        return guarded


class _Coordinator:
    """Lazy central client avoids construction cycles; every request remains guarded."""

    def __init__(self, owner: CompetitionAWSClients) -> None:
        self.owner = owner

    def __getattr__(self, name: str) -> Any:
        if name not in {"update_item", "exceptions"}:
            raise CompetitionClientFault("competition_coordinator_operation_unsupported")
        return getattr(self.owner.client("dynamodb", self.owner.profile.primary_region, 10), name)
