"""Serialize physical sends using a persistent owner and a monotonic safety interval."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import TypeVar
from uuid import uuid4

from appraisal_review.ports.model_dispatch import DispatchDenied, DispatchGuard, DispatchStore

T = TypeVar("T")


class SharedModelDispatcher:
    def __init__(
        self, store: DispatchStore, *, scope: str = "team-wide", interval_seconds: float = 1.1
    ) -> None:
        if not scope.strip() or not math.isfinite(interval_seconds) or interval_seconds < 1.1:
            raise ValueError("A shared scope and interval of at least 1.1 seconds are required")
        self.store, self.scope, self.interval_seconds = store, scope, interval_seconds

    @staticmethod
    def _wait(guard: DispatchGuard, duration: float) -> None:
        until = time.monotonic() + duration
        while True:
            guard.check()
            remaining = min(until, guard.deadline) - time.monotonic()
            if remaining <= 0:
                guard.check()
                return
            guard.cancelled.wait(min(0.05, remaining))

    def send(self, call: Callable[[], T], guard: DispatchGuard) -> T:
        owner = str(uuid4())
        while True:
            guard.check()
            try:
                acquired = self.store.try_acquire(self.scope, owner)
            except Exception:
                raise DispatchDenied("dispatch_store_unavailable") from None
            if acquired:
                break
            self._wait(guard, 0.05)
        try:
            # Every owner waits a full interval AFTER the preceding transport exits.
            # Client wall clocks and restart cannot shorten this monotonic interval.
            self._wait(guard, self.interval_seconds)
            guard.charge()
            return call()
        finally:
            try:
                self.store.release(self.scope, owner)
            except Exception:
                # Unknown release retains the durable owner. No implicit takeover.
                raise DispatchDenied("dispatch_release_unknown") from None
