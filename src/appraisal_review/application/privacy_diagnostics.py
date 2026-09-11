"""Request-local bounded observations; never grants or replaces authorization."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Literal, ParamSpec, TypeVar

from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.privacy_diagnostics import (
    LocalRestoreFailure,
    RestoreFailureCode,
    RestoreStage,
)
from appraisal_review.domain.privacy_mapping import MappingError, MappingFault
from appraisal_review.domain.privacy_models import PrivacyErrorCode

DiagnosticCode = RestoreFailureCode | MappingError | PrivacyErrorCode
Operation = Literal["restore", "download", "review"]
_ACTIVE: ContextVar[RestoreDiagnostics | None] = ContextVar("restore_diagnostics", default=None)
_STAGE: ContextVar[RestoreStage] = ContextVar("restore_stage", default=RestoreStage.REQUEST)


def _milliseconds(seconds: float) -> float:
    return round(min(max(seconds * 1000, 0.0), 86_400_000.0), 3) if math.isfinite(seconds) else 0.0


def _code(error: Exception) -> DiagnosticCode:
    if isinstance(error, LocalRestoreFailure) and type(error.diagnostic_code) is RestoreFailureCode:
        return error.diagnostic_code
    if isinstance(error, MappingFault) and type(error.code) is MappingError:
        return error.code
    if isinstance(error, PrivacyFault) and type(error.problem.code) is PrivacyErrorCode:
        return error.problem.code
    if isinstance(error, TimeoutError):
        return RestoreFailureCode.OPERATION_TIMED_OUT
    if isinstance(error, OSError):
        return RestoreFailureCode.LOCAL_IO_FAILED
    if isinstance(error, ValueError):
        return RestoreFailureCode.VALIDATION_FAILED
    return RestoreFailureCode.OPERATION_FAILED


@dataclass(frozen=True)
class Failure:
    stage: RestoreStage
    code: DiagnosticCode

    def record(self) -> dict[str, str]:
        return {"stage": self.stage.value, "code": self.code.value}


@dataclass
class _Timing:
    calls: int = 0
    failed_calls: int = 0
    seconds: float = 0.0


@dataclass
class RestoreDiagnostics:
    request_id: str
    operation: Operation
    started: float = field(default_factory=time.monotonic)
    failure: Failure | None = None
    timings: dict[RestoreStage, _Timing] = field(default_factory=dict)
    ocr: dict[str, object] = field(default_factory=dict)

    def failed(self, error: Exception, stage: RestoreStage) -> None:
        # Keep the first, innermost failure even when a publisher catches it and
        # returns False. Outer generic refusals cannot erase the observed cause.
        if self.failure is None:
            try:
                code = _code(error)
            except Exception:
                # A malformed adapter exception must not break the rejection it
                # describes or export an untrusted exception attribute.
                code = RestoreFailureCode.OPERATION_FAILED
            self.failure = Failure(stage, code)

    def record(self, status: int | None) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "operation": self.operation,
            "status": status,
            "code": (
                PrivacyErrorCode.VERIFICATION_FAILED.value
                if self.ocr
                else self.failure.code.value
                if self.failure
                else "ok"
            ),
            "stage": self.failure.stage.value if self.failure else "complete",
            **self.ocr,
            "failure": self.failure.record() if self.failure else None,
            "elapsed_ms": _milliseconds(time.monotonic() - self.started),
            "timings": [
                {
                    "stage": stage.value,
                    "calls": min(value.calls, 65535),
                    "failed_calls": min(value.failed_calls, 65535),
                    "elapsed_ms": _milliseconds(value.seconds),
                }
                for stage, value in sorted(self.timings.items())
            ],
        }


@contextmanager
def diagnostic_request(value: RestoreDiagnostics) -> Iterator[None]:
    token = _ACTIVE.set(value)
    stage_token = _STAGE.set(RestoreStage.REQUEST)
    try:
        yield
    finally:
        _STAGE.reset(stage_token)
        _ACTIVE.reset(token)


def note_failure(error: Exception) -> None:
    value = _ACTIVE.get()
    if value is not None:
        value.failed(error, _STAGE.get())


def note_ocr_failure(diagnostic: dict[str, object]) -> None:
    value = _ACTIVE.get()
    if value is None:
        return
    stage, reason = diagnostic.get("stage"), diagnostic.get("reason")
    if type(stage) is not str or type(reason) is not str:
        return
    if stage not in ("published", "restored") or reason not in (
        "uncertain_ocr",
        "ocr_deadline",
        "ocr_unavailable",
        "placeholder_mismatch",
    ):
        return
    value.ocr = {"stage": stage, "reason": reason}
    for name in ("page", "observation_count", "low_confidence_count"):
        number = diagnostic.get(name)
        if type(number) is int and 0 <= number <= 10_000_000:
            value.ocr[name] = number


@contextmanager
def restore_stage(stage: RestoreStage) -> Iterator[None]:
    value = _ACTIVE.get()
    if value is None:
        yield
        return
    token = _STAGE.set(stage)
    start = time.monotonic()
    timing = value.timings.setdefault(stage, _Timing())
    timing.calls += 1
    try:
        yield
    except Exception as error:
        timing.failed_calls += 1
        value.failed(error, stage)
        raise
    finally:
        timing.seconds += max(time.monotonic() - start, 0.0)
        _STAGE.reset(token)


P = ParamSpec("P")
T = TypeVar("T")


def diagnostic_stage(stage: RestoreStage) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def call(*args: P.args, **kwargs: P.kwargs) -> T:
            with restore_stage(stage):
                return function(*args, **kwargs)

        return call

    return decorate
