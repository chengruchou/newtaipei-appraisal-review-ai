"""Bounded execution with truthful attempt records and no overlapping timeout retry."""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from appraisal_review.adapters.aws.extraction_errors import (
    ExtractionError,
    provider_failure,
    safe_code,
)
from appraisal_review.application.extraction_budget import ExtractionLedger
from appraisal_review.domain.extraction_contracts import AttemptTelemetry, FailureCode
from appraisal_review.domain.extraction_models import PageProposal
from appraisal_review.ports.document_extraction import ExtractionBoundaryError
from appraisal_review.ports.model_dispatch import (
    DispatchDenied,
    DispatchGuard,
    dispatch_guard,
    inherited_dispatch_authority,
)


@dataclass(frozen=True)
class ExecutionRecord:
    proposal: PageProposal | None = field(repr=False)
    code: str | None
    attempts: tuple[AttemptTelemetry, ...]
    elapsed_seconds: float


@dataclass(frozen=True)
class _Reply:
    response: dict[str, Any] | None = field(repr=False)
    failure: FailureCode | None
    returned: bool = False


def usage(response: dict[str, Any] | None, key: str) -> int | None:
    data = response.get("usage") if response else None
    value = data.get(key) if isinstance(data, dict) else None
    return value if type(value) is int and value >= 0 else None


async def execute(
    call: Callable[[], dict[str, Any]],
    validate: Callable[[dict[str, Any]], PageProposal],
    *,
    ledger: ExtractionLedger,
    attempts: int,
    tokens: int,
    timeout: float,
    backoff_base: float,
    backoff_cap: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float], float] = lambda upper: random.uniform(0, upper),
    on_attempt: Callable[[AttemptTelemetry], None] | None = None,
) -> ExecutionRecord:
    started = clock()
    records: list[AttemptTelemetry] = []
    proposal = None
    code: str | None = None

    def invoke(guard: DispatchGuard) -> _Reply:
        try:
            if ledger.stopped or ledger.remaining <= 0:
                return _Reply(None, "budget_exhausted")
            with dispatch_guard(guard):
                response = call()
            if not isinstance(response, dict):
                return _Reply(None, "malformed_output", True)
            return _Reply(response, None, True)
        except DispatchDenied:
            return _Reply(None, "budget_exhausted")
        except ExtractionBoundaryError as error:
            return _Reply(None, error.code)
        except Exception as error:
            error_response = getattr(error, "response", None)
            return _Reply(
                error_response if isinstance(error_response, dict) else None,
                provider_failure(error),
            )
        finally:
            ledger.finished_call()

    for attempt in range(1, min(attempts, ledger.limits.max_attempts_per_page) + 1):
        try:
            ledger.reserve_call(tokens)
        except ExtractionBoundaryError as error:
            code = error.code
            break
        attempt_start = clock()
        completion: Literal["returned", "failed", "unknown"] = "returned"
        wait_limit = min(timeout, ledger.remaining)
        authority = inherited_dispatch_authority()

        def permitted(authority: Callable[[], bool] = authority) -> bool:
            return authority() and not ledger.stopped and ledger.remaining > 0

        guard = DispatchGuard(
            deadline=time.monotonic() + wait_limit,
            authority=permitted,
        )
        task = asyncio.get_running_loop().run_in_executor(None, invoke, guard)
        # Shield preserves the SDK worker and its reservation after caller timeout.
        try:
            reply = await asyncio.wait_for(asyncio.shield(task), wait_limit)
        except TimeoutError:
            guard.cancelled.set()
            reply = _Reply(None, "timeout")
        except asyncio.CancelledError:
            guard.cancelled.set()
            ledger.stop()
            if on_attempt is not None:
                # Preserve cancellation even if the optional observer fails.
                with suppress(Exception):
                    on_attempt(
                        AttemptTelemetry(
                            attempt=attempt,
                            completion="unknown",
                            failure="timeout",
                            input_tokens=None,
                            output_tokens=None,
                            elapsed_seconds=max(0.0, clock() - attempt_start),
                            backoff_seconds=0,
                        )
                    )
            raise
        code = reply.failure
        input_tokens = usage(reply.response, "inputTokens")
        output_tokens = usage(reply.response, "outputTokens")
        ledger.settle_tokens(tokens, output_tokens)
        if code == "timeout":
            completion = "unknown"
            ledger.stop()
        elif code is not None and not reply.returned:
            completion = "failed"
        else:
            try:
                assert reply.response is not None
                proposal = validate(reply.response)
            except ExtractionError as error:
                code = error.code
            except Exception:
                code = "malformed_output"
        if output_tokens is not None and output_tokens > tokens:
            code, proposal = "budget_exhausted", None
        if ledger.remaining <= 0 and code is None:
            code, proposal = "budget_exhausted", None
        elapsed = max(0.0, clock() - attempt_start)
        attempt_code = code
        note = AttemptTelemetry(
            attempt=attempt,
            completion=completion,
            failure=safe_code(attempt_code) if attempt_code else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
            backoff_seconds=0,
        )
        if on_attempt is not None:
            try:
                on_attempt(note)
            except Exception:
                ledger.stop()
                code, proposal = "provider_error", None
        backoff = 0.0
        retry = code in {"throttled", "service_unavailable"} and attempt < attempts
        if retry:
            ceiling = min(backoff_cap, backoff_base * 2 ** (attempt - 1))
            delay = jitter(ceiling)
            if not math.isfinite(delay) or not 0 <= delay <= ceiling:
                code, retry = "configuration_error", False
            elif delay >= ledger.remaining:
                code, retry = "budget_exhausted", False
            else:
                before = clock()
                try:
                    await asyncio.wait_for(sleep(delay), ledger.remaining)
                except TimeoutError:
                    code, retry = "budget_exhausted", False
                backoff = max(0.0, clock() - before)
        records.append(note.model_copy(update={"backoff_seconds": backoff}))
        if not retry:
            break
    return ExecutionRecord(
        proposal if code is None else None, code, tuple(records), max(0.0, clock() - started)
    )
