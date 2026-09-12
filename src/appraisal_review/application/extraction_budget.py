"""Thread-safe, single-run admission and conservative token reservations."""

import threading
import time
from collections.abc import Callable

from appraisal_review.domain.extraction_contracts import ExecutionBudget
from appraisal_review.ports.document_extraction import ExtractionBoundaryError


class ExtractionLedger:
    def __init__(self, limits: ExecutionBudget, *, clock: Callable[[], float] = time.monotonic):
        self.limits = ExecutionBudget.model_validate(limits)
        self.clock = clock
        self._lock = threading.Lock()
        self._started: float | None = None
        self._run: str | None = None
        self.pages = self.calls = self.in_flight = self.reserved_output_tokens = 0
        self.stopped = False

    def begin_page(self, run_binding: str) -> None:
        with self._lock:
            if self._run is not None and self._run != run_binding:
                raise ExtractionBoundaryError("configuration_error")
            if self._started is None:
                self._started, self._run = self.clock(), run_binding
            if self.stopped or self.pages >= self.limits.max_pages or self.remaining <= 0:
                raise ExtractionBoundaryError("budget_exhausted")
            self.pages += 1

    @property
    def remaining(self) -> float:
        if self._started is None:
            return self.limits.max_elapsed_seconds
        return max(0.0, self.limits.max_elapsed_seconds - (self.clock() - self._started))

    def reserve_call(self, tokens: int) -> None:
        with self._lock:
            if (
                self._started is None
                or self.stopped
                or self.remaining <= 0
                or self.calls >= self.limits.max_calls
                or self.in_flight >= self.limits.max_concurrency
                or self.reserved_output_tokens + tokens > self.limits.max_output_tokens
            ):
                raise ExtractionBoundaryError("budget_exhausted")
            self.calls += 1
            self.in_flight += 1
            self.reserved_output_tokens += tokens

    def finished_call(self) -> None:
        """Only the SDK worker releases its slot, including after caller timeout."""
        with self._lock:
            self.in_flight -= 1

    def settle_tokens(self, reserved: int, observed: int | None) -> None:
        with self._lock:
            if observed is None:
                return
            self.reserved_output_tokens += observed - reserved
            if observed > reserved or self.reserved_output_tokens > self.limits.max_output_tokens:
                self.stopped = True

    def stop(self) -> None:
        with self._lock:
            self.stopped = True
