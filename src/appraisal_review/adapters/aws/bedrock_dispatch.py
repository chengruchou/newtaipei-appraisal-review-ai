"""Botocore physical transport boundary, covering every SDK retry and operation."""

from __future__ import annotations

import json
from copy import copy
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from appraisal_review.adapters.aws.competition_wire import (
    CompetitionWireFault,
    WireBinding,
    install_wire_binding,
)
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.domain.competition_data import DataPart
from appraisal_review.ports.competition_data import CompetitionDataAdmission
from appraisal_review.ports.model_dispatch import DispatchDenied, current_dispatch_guard

_AUTH_HEADERS = {"authorization", "x-amz-date", "date", "x-amz-security-token"}
_METADATA_HEADERS = {
    "accept",
    "content-type",
    "content-length",
    "user-agent",
    "amz-sdk-invocation-id",
    "amz-sdk-request",
    "x-amz-content-sha256",
    "x-amzn-bedrock-trace",
    "x-amzn-bedrock-guardrailidentifier",
    "x-amzn-bedrock-guardrailversion",
    "x-amzn-bedrock-performanceconfig-latency",
    "x-amzn-bedrock-service-tier",
}


class _DispatchTransport:
    def __init__(
        self,
        transport: Any,
        dispatcher: SharedModelDispatcher,
        competition_admission: CompetitionDataAdmission | None,
        *,
        wire_binding: WireBinding,
        endpoint_url: str,
        allow_loopback_for_testing: bool = False,
    ) -> None:
        self.transport, self.dispatcher = transport, dispatcher
        self.competition_admission = competition_admission
        self.wire_binding = wire_binding
        try:
            self.endpoint = urlsplit(endpoint_url)
        except ValueError:
            raise DispatchDenied("dispatch_destination_invalid") from None
        self.allow_loopback_for_testing = allow_loopback_for_testing

    def send(self, request: Any) -> Any:
        guard = current_dispatch_guard()
        if self.competition_admission is None:
            raise DispatchDenied("competition_admission_required")
        body = request.body
        if body is None:
            body = b""
        if (
            type(body) is not bytes
            or type(request.url) is not str
            or type(request.method) is not str
        ):
            raise DispatchDenied("dispatch_immutable_envelope_required")
        try:
            target = urlsplit(request.url)
        except ValueError:
            raise DispatchDenied("dispatch_destination_invalid") from None
        if (
            target.netloc != self.endpoint.netloc
            or target.scheme != self.endpoint.scheme
            or target.username is not None
            or target.password is not None
            or target.fragment
            or (
                target.scheme != "https"
                and not (
                    self.allow_loopback_for_testing
                    and target.scheme == "http"
                    and target.hostname in {"127.0.0.1", "::1", "localhost"}
                )
            )
        ):
            raise DispatchDenied("dispatch_destination_invalid")
        headers = dict(request.headers.items())
        metadata = []
        seen = set()
        for name, value in headers.items():
            if type(name) is not str or name.lower() in seen:
                raise DispatchDenied("dispatch_header_unsupported")
            key = name.lower()
            seen.add(key)
            if key in _AUTH_HEADERS:
                continue
            if key not in _METADATA_HEADERS or type(value) not in {str, bytes}:
                raise DispatchDenied("dispatch_header_unsupported")
            encoded = value.encode("utf-8") if isinstance(value, str) else value
            metadata.append((key, encoded.hex()))
        # Preserve serialized method, body, URL and reviewed metadata across checking/use.
        prepared = copy(request)
        prepared.body, prepared.url, prepared.method = body, request.url, request.method
        prepared.headers = MappingProxyType(headers)
        try:
            operation = self.wire_binding.check(prepared)
        except CompetitionWireFault:
            raise DispatchDenied("dispatch_wire_operation_changed") from None
        parts = (
            DataPart("bedrock.request.body", "model_prompt", body),
            DataPart("bedrock.request.url", "metadata", prepared.url.encode("utf-8")),
            DataPart(
                "bedrock.request.headers",
                "metadata",
                json.dumps(sorted(metadata), separators=(",", ":")).encode(),
            ),
            DataPart("bedrock.request.operation", "metadata", operation),
        )

        def admitted_send() -> Any:
            assert self.competition_admission is not None
            self.competition_admission.check(parts)
            guard.check()
            return self.transport.send(prepared)

        return self.dispatcher.send(admitted_send, guard)

    def close(self) -> None:
        self.transport.close()


def install_bedrock_dispatch(
    client: Any,
    dispatcher: SharedModelDispatcher,
    *,
    competition_admission: CompetitionDataAdmission | None = None,
    allow_loopback_for_testing: bool = False,
) -> Any:
    """Install once before sharing the SDK client; reject unsupported transport seams.

    The botocore private seam is intentionally isolated and covered by real SDK to
    localhost HTTP regressions. Production SDK retries are disabled; even injected
    implicit retries must pass the physical-send guard and cannot exceed its budget.
    """
    if client.meta.service_model.service_name not in {"bedrock", "bedrock-runtime"}:
        raise DispatchDenied("dispatch_service_unsupported")
    transport = client._endpoint.http_session
    if isinstance(transport, _DispatchTransport):
        if (
            transport.dispatcher is not dispatcher
            or transport.competition_admission is not competition_admission
            or transport.allow_loopback_for_testing != allow_loopback_for_testing
        ):
            raise DispatchDenied("dispatch_coordinator_conflict")
        return client
    from botocore.httpsession import URLLib3Session

    if type(transport) is not URLLib3Session:
        # Other transports may retry internally without re-entering this boundary.
        raise DispatchDenied("dispatch_transport_unsupported")
    client._endpoint.http_session = _DispatchTransport(
        transport,
        dispatcher,
        competition_admission,
        wire_binding=install_wire_binding(client),
        endpoint_url=client.meta.endpoint_url,
        allow_loopback_for_testing=allow_loopback_for_testing,
    )
    return client


def require_bedrock_dispatch(client: Any) -> None:
    """Injected synthetic clients have no SDK endpoint; real clients must be covered."""
    endpoint = getattr(client, "__dict__", {}).get("_endpoint")
    if endpoint is not None and not isinstance(endpoint.http_session, _DispatchTransport):
        raise DispatchDenied("dispatch_coordinator_required")


class BedrockDispatchClients:
    def __init__(
        self,
        clients: Any,
        dispatcher: SharedModelDispatcher,
        *,
        competition_admission: CompetitionDataAdmission | None = None,
    ) -> None:
        self.clients, self.dispatcher = clients, dispatcher
        self.competition_admission = competition_admission

    def client(self, service: str, region: str, timeout: float) -> Any:
        client = self.clients.client(service, region, timeout)
        if service in {"bedrock", "bedrock-runtime"}:
            return install_bedrock_dispatch(
                client, self.dispatcher, competition_admission=self.competition_admission
            )
        return client
