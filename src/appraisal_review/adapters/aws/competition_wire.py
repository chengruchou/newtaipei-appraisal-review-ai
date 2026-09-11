"""Bind each final physical request to its serialized SDK operation and destination."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


class CompetitionWireFault(RuntimeError):
    """A prepared request changed operation, method, path, query or destination."""


@dataclass(frozen=True)
class _Expected:
    service: str
    operation: str
    method: str
    url: str


class WireBinding:
    def __init__(self, service: str) -> None:
        self.service = service
        self._expected: ContextVar[_Expected | None] = ContextVar(
            f"competition_wire_{service}", default=None
        )

    def capture(self, model: Any, params: dict[str, Any], **kwargs: Any) -> None:
        method, url = model.http.get("method"), params.get("url")
        if type(method) is not str or type(url) is not str or params.get("method") != method:
            raise CompetitionWireFault("competition_operation_schema")
        self._expected.set(_Expected(self.service, model.name, method, url))

    def check(self, request: Any) -> bytes:
        expected = self._expected.get()
        if expected is None or request.method != expected.method or request.url != expected.url:
            raise CompetitionWireFault("competition_wire_operation_changed")
        return json.dumps(
            {
                "service": expected.service,
                "operation": expected.operation,
                "method": expected.method,
                "url": expected.url,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


def install_wire_binding(client: Any) -> WireBinding:
    current = client.__dict__.get("_competition_wire_binding")
    if current is not None:
        if type(current) is not WireBinding:
            raise CompetitionWireFault("competition_wire_binding_conflict")
        return current
    binding = WireBinding(client.meta.service_model.service_name)
    client.meta.events.register_first(
        "before-call.*.*", binding.capture, unique_id="competition-wire-v1"
    )
    client.__dict__["_competition_wire_binding"] = binding
    return binding
