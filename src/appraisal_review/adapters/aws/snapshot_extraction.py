"""Authorized Bedrock page outcomes with shared run budgets and safe telemetry."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, Protocol

from appraisal_review.adapters.aws.document_extraction import (
    PROMPT_DIGEST,
    PROMPT_VERSION,
    BedrockDocumentExtractor,
    ExtractionConfig,
)
from appraisal_review.adapters.aws.extraction_errors import ExtractionError, safe_code
from appraisal_review.adapters.aws.extraction_execution import ExecutionRecord, execute
from appraisal_review.adapters.aws.extraction_preflight import (
    AWSClients,
    BedrockAccessPolicy,
    preflight,
)
from appraisal_review.application.extraction_budget import ExtractionLedger
from appraisal_review.domain.extraction_contracts import (
    AttemptTelemetry,
    ExecutionBudget,
    HandoffLocation,
    HandoffRequest,
    PageOutcome,
    PageRequest,
    ProviderConfiguration,
    ProviderTelemetry,
)
from appraisal_review.ports.document_extraction import (
    AuthorizedSanitizedSnapshot,
    ExtractionBoundaryError,
)


class SnapshotRenderer(Protocol):
    async def render(self, snapshot: AuthorizedSanitizedSnapshot, page: int) -> bytes: ...


class BedrockSnapshotBackend:
    def __init__(
        self,
        *,
        clients: AWSClients,
        policy: BedrockAccessPolicy,
        config: ExtractionConfig,
        budget: ExecutionBudget,
        renderer: SnapshotRenderer,
        clock: Callable[[], float] = time.monotonic,
        on_attempt: Callable[[PageRequest, AttemptTelemetry], None] | None = None,
    ) -> None:
        self.clients, self.policy, self.config = clients, policy, config
        self.budget = budget.model_copy(
            update={
                "max_elapsed_seconds": min(budget.max_elapsed_seconds, config.total_timeout_seconds)
            }
        )
        self.renderer, self.clock = renderer, clock
        self.on_attempt = on_attempt
        self.ledger = ExtractionLedger(self.budget, clock=clock)
        self._preflight_lock = asyncio.Lock()

    async def extract(
        self, request: PageRequest, snapshot: AuthorizedSanitizedSnapshot
    ) -> PageOutcome:
        source = snapshot.validate_request(request)
        policy = BedrockAccessPolicy.model_validate(self.policy)
        config = ExtractionConfig.model_validate(self.config)
        budget = ExecutionBudget.model_validate(self.budget)
        if (
            budget != self.ledger.limits
            or config.model_id != policy.model_id
            or config.region != policy.region
            or config.timeout_seconds != policy.timeout_seconds
            or config.max_output_tokens > policy.max_output_tokens
            or config.max_output_tokens > budget.max_output_tokens
            or config.attempts > budget.max_attempts_per_page
            or config.max_input_characters > budget.max_context_characters
            or config.timeout_seconds > budget.max_elapsed_seconds
            or config.max_image_bytes > policy.max_image_bytes
            or config.max_image_width > policy.max_image_width
            or config.max_image_height > policy.max_image_height
            or config.max_image_pixels > policy.max_image_pixels
        ):
            raise ExtractionBoundaryError("configuration_error")
        started = self.clock()
        record = ExecutionRecord(None, None, (), 0)
        try:
            self.ledger.begin_page(request.run.model_dump_json())
            if len(snapshot.content) > budget.max_input_bytes:
                raise ExtractionBoundaryError("budget_exhausted")
            image = await asyncio.wait_for(
                self.renderer.render(snapshot, request.page), self.ledger.remaining
            )
            context = request.context.model_dump_json()
            payload = BedrockDocumentExtractor._request_payload(
                source, request.page, image, config=config, context=context
            )
            if self.ledger.remaining <= 0:
                raise ExtractionBoundaryError("budget_exhausted")

            async def checked_client() -> Any:
                async with self._preflight_lock:
                    if self.ledger.stopped or self.ledger.remaining <= 0:
                        raise ExtractionBoundaryError("budget_exhausted")
                    future = asyncio.get_running_loop().run_in_executor(
                        None, preflight, self.clients, policy
                    )
                    future.add_done_callback(lambda f: None if f.cancelled() else f.exception())
                    return await asyncio.shield(future)

            client = await asyncio.wait_for(checked_client(), self.ledger.remaining)
            extractor = BedrockDocumentExtractor(client, config)
            observer = self.on_attempt
            record = await execute(
                lambda: extractor._converse(payload, image),
                lambda response: extractor._validate_response(response, source, request.page),
                ledger=self.ledger,
                attempts=config.attempts,
                tokens=config.max_output_tokens,
                timeout=config.timeout_seconds,
                backoff_base=config.backoff_base_seconds,
                backoff_cap=config.backoff_cap_seconds,
                clock=self.clock,
                on_attempt=(lambda note: observer(request, note)) if observer else None,
            )
        except TimeoutError:
            self.ledger.stop()
            record = ExecutionRecord(None, "timeout", (), 0)
        except asyncio.CancelledError:
            self.ledger.stop()
            raise
        except (ExtractionBoundaryError, ExtractionError) as error:
            record = ExecutionRecord(None, error.code, (), 0)
        except Exception:
            record = ExecutionRecord(None, "unsupported_input", (), 0)
        configuration = ProviderConfiguration(
            provider="bedrock",
            model_id=config.model_id,
            region=config.region,
            api="converse",
            routing_regions=policy.allowed_regions,
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
        )
        telemetry = ProviderTelemetry(
            configuration=configuration,
            prompt_version=PROMPT_VERSION,
            prompt_digest=PROMPT_DIGEST,
            attempts=record.attempts,
            elapsed_seconds=max(0.0, self.clock() - started),
        )

        def outcome(code: str | None) -> PageOutcome:
            return PageOutcome(
                request=request,
                status="failed" if code else "candidate",
                proposal=None if code else record.proposal,
                failure=safe_code(code) if code else None,
                telemetry=telemetry,
                handoffs=(
                    HandoffRequest(
                        request=request,
                        reason="page_failed",
                        locations=(HandoffLocation(page=request.page),),
                    ),
                )
                if code
                else (),
            )

        try:
            return snapshot.validate_outcome(outcome(record.code))
        except ValueError:
            # Identity/purpose/geometry failure cannot publish a candidate. Keep billed attempts.
            if telemetry.attempts:
                updated = (
                    *telemetry.attempts[:-1],
                    telemetry.attempts[-1].model_copy(
                        update={"failure": "invalid_source_reference"}
                    ),
                )
                telemetry = telemetry.model_copy(update={"attempts": updated})
            return outcome("invalid_source_reference")
