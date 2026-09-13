"""Typed registry of official open-data sources and their verification status.

The registry file (configs/sources/registry-v1.json) lists every official
source the user supplied, and it is the only place a dataset endpoint may come
from: adapters take a SourceEntry, never a hardcoded URL. Each entry carries an
explicit verification status with a reason and a check timestamp, so nothing
listed here is presented as verified data merely because it appears on the
list. Status changes never mutate the loaded registry in place: update_status
returns a new registry object whose JSON the integrator persists deliberately.

Statuses:
- listed: on the official source list; nothing about the endpoint verified.
- metadata_verified: dataset id and endpoint pattern recorded; no live rows.
- data_verified: a real fetch returned parseable rows (integrator-performed).
- enabled: verified and approved for candidate production.
- unavailable: a check failed; the reason says what was observed.

Transitions advance one step at a time (or fall to unavailable), because each
step requires its own evidence; an unavailable source must be re-listed and
re-verified rather than jumping straight back to enabled.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.service_contracts import (
    ContractModel,
    OpaqueID,
    ServiceErrorCode,
)

SourceStatus = Literal["listed", "metadata_verified", "data_verified", "enabled", "unavailable"]

#: Every status a fetch adapter may act on. "listed" is deliberately excluded:
#: nothing may be fetched from an entry whose endpoint was never even recorded.
FETCHABLE_STATUSES: frozenset[str] = frozenset({"metadata_verified", "data_verified", "enabled"})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "listed": frozenset({"metadata_verified", "unavailable"}),
    "metadata_verified": frozenset({"data_verified", "unavailable"}),
    "data_verified": frozenset({"enabled", "unavailable"}),
    "enabled": frozenset({"unavailable"}),
    "unavailable": frozenset({"listed"}),
}


class RegistryModel(ContractModel):
    schema_version: Literal["source-registry-v1"] = "source-registry-v1"


class DeclaredFieldMap(RegistryModel):
    """Configured field-name guesses for a dataset's rows.

    These are configuration, not knowledge: nobody has seen live rows until the
    integrator's real fetch, so the flag stays True until a human inspects an
    actual payload and the integrator updates the registry copy.
    """

    name_field: str | None = Field(default=None, min_length=1, max_length=100)
    lat_field: str | None = Field(default=None, min_length=1, max_length=100)
    lng_field: str | None = Field(default=None, min_length=1, max_length=100)
    unverified_until_live_fetch: bool = True


class SourceEntry(RegistryModel):
    source_id: OpaqueID
    title: str = Field(min_length=1, max_length=200)
    publisher: str = Field(min_length=1, max_length=200)
    portal_url: str = Field(min_length=1, max_length=500)
    status: SourceStatus
    status_reason: str = Field(min_length=1, max_length=500)
    checked_at: str = Field(min_length=1, max_length=64)
    api_endpoint: str | None = Field(default=None, min_length=1, max_length=500)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=100)
    declared_fields: DeclaredFieldMap | None = None
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def coherent_entry(self) -> SourceEntry:
        if not self.portal_url.startswith("https://"):
            raise ValueError("A portal URL must be https")
        if self.api_endpoint is not None and not self.api_endpoint.startswith("https://"):
            raise ValueError("An API endpoint must be https")
        if self.status in FETCHABLE_STATUSES:
            if self.api_endpoint is None or self.dataset_id is None:
                raise ValueError("A verified source requires its endpoint and dataset id")
            if self.declared_fields is None:
                raise ValueError("A verified source requires a declared field map")
        if (
            self.api_endpoint is not None
            and self.dataset_id is not None
            and self.dataset_id not in self.api_endpoint
        ):
            raise ValueError("The API endpoint must reference the entry's dataset id")
        return self


class SourceRegistry(RegistryModel):
    registry_id: OpaqueID
    entries: tuple[SourceEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_sources(self) -> SourceRegistry:
        identifiers = [entry.source_id for entry in self.entries]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Registry source ids must be unique")
        return self


def load_registry(path: Path) -> SourceRegistry:
    """Load and validate one registry file; a malformed file is a typed refusal."""
    try:
        return SourceRegistry.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as error:
        raise ServiceFault(ServiceErrorCode.VALIDATION) from error


def get_entry(registry: SourceRegistry, source_id: str) -> SourceEntry:
    for entry in registry.entries:
        if entry.source_id == source_id:
            return entry
    raise ServiceFault(ServiceErrorCode.NOT_FOUND)


def update_status(
    registry: SourceRegistry,
    source_id: str,
    *,
    status: SourceStatus,
    reason: str,
    checked_at: str,
) -> SourceRegistry:
    """Return a NEW registry with one entry's status advanced.

    The input registry is never mutated (entries are frozen models); the caller
    persists the returned registry's JSON deliberately. Re-asserting the current
    status is allowed as a re-check (it refreshes reason and checked_at); any
    other change must be a single allowed transition.
    """
    current = get_entry(registry, source_id)
    if status != current.status and status not in _ALLOWED_TRANSITIONS[current.status]:
        raise ServiceFault(ServiceErrorCode.CONFLICT)
    try:
        updated = SourceEntry.model_validate(
            current.model_dump(mode="json")
            | {"status": status, "status_reason": reason, "checked_at": checked_at}
        )
    except ValidationError as error:
        raise ServiceFault(ServiceErrorCode.VALIDATION) from error
    entries = tuple(
        updated if entry.source_id == source_id else entry for entry in registry.entries
    )
    return SourceRegistry.model_validate(
        {
            "registry_id": registry.registry_id,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        }
    )


def registry_json(registry: SourceRegistry) -> str:
    """Canonical pretty JSON of a registry, for the integrator to persist."""
    return json.dumps(
        registry.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=False
    )
