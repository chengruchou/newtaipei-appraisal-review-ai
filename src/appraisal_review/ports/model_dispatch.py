"""Trusted, provider-neutral admission immediately before a physical model send."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event
from typing import Protocol


class DispatchDenied(Exception):
    """No physical send is permitted; diagnostic contains no request material."""


class DispatchStore(Protocol):
    def try_acquire(self, scope: str, owner: str) -> bool:
        """Atomically take an idle owner slot, with no expiry or clock comparison."""
        ...

    def release(self, scope: str, owner: str) -> None:
        """Conditionally release only the exact owner, otherwise fail closed."""
        ...


@dataclass
class DispatchGuard:
    deadline: float
    authority: Callable[[], bool] = lambda: True
    cancelled: Event = field(default_factory=Event)
    max_sends: int = 1
    sends: int = 0

    def check(self) -> None:
        if (
            not math.isfinite(self.deadline)
            or time.monotonic() >= self.deadline
            or self.cancelled.is_set()
            or not self.authority()
            or time.monotonic() >= self.deadline
            or self.cancelled.is_set()
        ):
            raise DispatchDenied("dispatch_authority_expired")

    def charge(self) -> None:
        self.check()
        if self.sends >= self.max_sends:
            raise DispatchDenied("dispatch_attempt_budget_exhausted")
        self.sends += 1


_current: ContextVar[DispatchGuard | None] = ContextVar("model_dispatch_guard", default=None)
_authority: ContextVar[Callable[[], bool] | None] = ContextVar(
    "model_dispatch_authority", default=None
)


def current_dispatch_guard() -> DispatchGuard:
    guard = _current.get()
    if guard is None:
        raise DispatchDenied("dispatch_guard_required")
    return guard


def inherited_dispatch_authority() -> Callable[[], bool]:
    return _authority.get() or (lambda: True)


@contextmanager
def dispatch_authority(authority: Callable[[], bool]) -> Iterator[None]:
    """Bind a trusted lease/fence check; callback must be safe from SDK worker threads."""
    parent = _authority.get()
    token = _authority.set(lambda: (parent is None or parent()) and authority())
    try:
        yield
    finally:
        _authority.reset(token)


@contextmanager
def dispatch_guard(guard: DispatchGuard) -> Iterator[None]:
    token = _current.set(guard)
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def dispatch_async_authority(require_current: Callable[[], Awaitable[object]]) -> Iterator[None]:
    """Recheck current durable authority on its event loop from a physical SDK thread.

    Closing the execution scope revokes even a shielded SDK worker that outlives it.
    A timeout, cancellation or failed check denies sending without replaying writes.
    """
    loop = asyncio.get_running_loop()
    stopped = Event()

    def permitted() -> bool:
        if stopped.is_set() or loop.is_closed() or not loop.is_running():
            return False

        async def verify() -> None:
            await require_current()

        assertion = verify()
        try:
            future = asyncio.run_coroutine_threadsafe(assertion, loop)
        except RuntimeError:
            assertion.close()
            return False
        try:
            future.result(timeout=0.5)
        except Exception:
            future.cancel()
            return False
        return not stopped.is_set()

    with dispatch_authority(permitted):
        try:
            yield
        finally:
            stopped.set()
