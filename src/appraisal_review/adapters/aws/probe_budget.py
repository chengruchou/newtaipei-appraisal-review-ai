"""Dated token-price bounds for the explicit synthetic foundation-model probe."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from threading import Lock
from typing import Any

from pydantic import Field

from appraisal_review.adapters.aws.extraction_preflight import WorkstationClients
from appraisal_review.domain.extraction_contracts import ContractModel
from appraisal_review.ports.document_extraction import ExtractionBoundaryError


class ProbePricing(ContractModel):
    model_id: str = Field(min_length=1)
    region: str = Field(min_length=1)
    rates_date: date
    rates_source: str = Field(min_length=1, max_length=1024)
    input_per_million_usd: Decimal = Field(gt=0)
    output_per_million_usd: Decimal = Field(gt=0)
    maximum_input_tokens: int = Field(gt=0, le=2_000_000, strict=True)

    def ceiling(self, calls: int, output_tokens: int, approved: Decimal) -> Decimal:
        if not approved.is_finite() or approved <= 0:
            raise ValueError("Invalid approved budget")
        if not 0 <= (date.today() - self.rates_date).days <= 30:
            raise ValueError("Dated pricing evidence required")
        estimate = (
            calls * self.maximum_input_tokens * self.input_per_million_usd
            + output_tokens * self.output_per_million_usd
        ) / Decimal(1_000_000)
        if estimate > approved:
            raise ValueError("Worst-case token estimate exceeds approved budget")
        return estimate


class PricedRuntime:
    def __init__(self, client: Any, pricing: ProbePricing, can_invoke: Callable[[], bool]) -> None:
        self.client, self.pricing, self.meta = client, pricing, client.meta
        self.count_calls = self.converse_calls = 0
        self.lock = Lock()
        self.can_invoke = can_invoke

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        if not self.can_invoke():
            raise ExtractionBoundaryError("budget_exhausted")
        if kwargs.get("modelId") != self.pricing.model_id or set(kwargs) - {
            "modelId",
            "system",
            "messages",
            "inferenceConfig",
        }:
            raise ExtractionBoundaryError("configuration_error")
        # Count the exact authorized system/messages/images before every paid retry.
        with self.lock:
            self.count_calls += 1
        counted = self.client.count_tokens(
            modelId=self.pricing.model_id,
            input={
                "converse": {
                    "system": kwargs["system"],
                    "messages": kwargs["messages"],
                }
            },
        )
        tokens = counted.get("inputTokens")
        if type(tokens) is not int or not 0 <= tokens <= self.pricing.maximum_input_tokens:
            raise ExtractionBoundaryError("budget_exhausted")
        if not self.can_invoke():
            # A late CountTokens response cannot start inference after the ledger stopped.
            raise ExtractionBoundaryError("budget_exhausted")
        with self.lock:
            self.converse_calls += 1
        response: dict[str, Any] = self.client.converse(**kwargs)
        return response


class PricedClients(WorkstationClients):
    def __init__(self, *, profile: str, pricing: ProbePricing) -> None:
        super().__init__(profile=profile)
        self.pricing = pricing
        self.runtimes: list[PricedRuntime] = []
        self.can_invoke: Callable[[], bool] = lambda: False

    def client(self, service: str, region: str, timeout: float) -> Any:
        client = super().client(service, region, timeout)
        if service == "bedrock-runtime":
            wrapped = PricedRuntime(client, self.pricing, lambda: self.can_invoke())
            self.runtimes.append(wrapped)
            return wrapped
        return client
