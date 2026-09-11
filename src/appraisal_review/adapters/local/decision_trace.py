"""Explicitly non-durable in-memory decision trace for local tests only."""

from __future__ import annotations

import asyncio
from uuid import UUID

from appraisal_review.domain.service_contracts import DecisionEvent, SelectionFailureEvent


class NonDurableInMemoryDecisionTrace:
    """Process-local test adapter; it provides no crash recovery or cross-process CAS."""

    def __init__(self) -> None:
        self._events: dict[UUID, list[str]] = {}
        self._failures: dict[UUID, list[str]] = {}
        self._lock = asyncio.Lock()

    async def append(self, event: DecisionEvent) -> None:
        detached = DecisionEvent.model_validate_json(event.model_dump_json())
        run_id = detached.proposal.run.run_id
        async with self._lock:
            serialized = self._events.setdefault(run_id, [])
            existing = tuple(DecisionEvent.model_validate_json(item) for item in serialized)
            existing_ids = {item.event_id for item in existing} | {
                SelectionFailureEvent.model_validate_json(item).event_id
                for item in self._failures.get(run_id, ())
            }
            if detached.event_id in existing_ids:
                raise ValueError("Decision event ID already exists")
            if not set(detached.parent_event_ids) <= existing_ids:
                raise ValueError("Decision event parents must already exist in this run")
            serialized.append(detached.model_dump_json())

    async def append_failure(self, event: SelectionFailureEvent) -> None:
        detached = SelectionFailureEvent.model_validate_json(event.model_dump_json())
        run_id = detached.run.run_id
        async with self._lock:
            ids = {
                DecisionEvent.model_validate_json(item).event_id
                for item in self._events.get(run_id, ())
            }
            ids |= {
                SelectionFailureEvent.model_validate_json(item).event_id
                for item in self._failures.get(run_id, ())
            }
            if detached.event_id in ids or not set(detached.parent_event_ids) <= ids:
                raise ValueError("Selection event requires a unique ID and existing parents")
            self._failures.setdefault(run_id, []).append(detached.model_dump_json())

    async def read_failures(self, run_id: UUID) -> tuple[SelectionFailureEvent, ...]:
        async with self._lock:
            return tuple(
                SelectionFailureEvent.model_validate_json(item)
                for item in self._failures.get(run_id, ())
            )

    async def read(self, run_id: UUID) -> tuple[DecisionEvent, ...]:
        async with self._lock:
            return tuple(
                DecisionEvent.model_validate_json(item) for item in self._events.get(run_id, ())
            )
