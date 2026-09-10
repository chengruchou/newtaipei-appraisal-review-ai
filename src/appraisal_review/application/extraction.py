"""One source-authorized extraction assembly; no implicit resolver or provider."""

from __future__ import annotations

from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.extraction_contracts import (
    ExecutionBudget,
    PageOutcome,
    PageRequest,
    ProviderConfiguration,
)
from appraisal_review.domain.service_contracts import Permission
from appraisal_review.ports.document_extraction import (
    ExtractionBoundaryError,
    PageExtractionProvider,
    SanitizedSnapshotResolver,
)


def request_plan(
    request: PageRequest, configuration: ProviderConfiguration, budget: ExecutionBudget
) -> dict[str, object]:
    """No SDK, credentials, source reads or provider calls. Metadata is not access proof."""
    try:
        request = PageRequest.model_validate(request)
        configuration = ProviderConfiguration.model_validate(configuration)
        budget = ExecutionBudget.model_validate(budget)
        if configuration.max_output_tokens > budget.max_output_tokens:
            raise ValueError
    except Exception:
        raise ExtractionBoundaryError("configuration_error") from None
    return {
        "mode": "dry_run",
        "scheduled_pages": 1,
        "page": request.page,
        "task": request.context.task,
        "language": request.context.language,
        "configuration": configuration.model_dump(mode="json"),
        "budget": budget.model_dump(mode="json"),
        "source_authorization": "not_checked",
        "privacy_integration": "unavailable",
        "model_access": "not_checked",
        "provider_calls": 0,
        "ready_for_live": False,
    }


class AuthorizedExtractionService:
    def __init__(
        self,
        *,
        resolver: SanitizedSnapshotResolver | None = None,
        backend: PageExtractionProvider | None = None,
    ) -> None:
        self.resolver, self.backend = resolver, backend

    async def extract(self, principal: Principal, request: PageRequest) -> PageOutcome:
        try:
            request = PageRequest.model_validate(request)
            principal.require(request.source.document.case_id, Permission.REVIEW)
            if self.resolver is None:
                raise ExtractionBoundaryError("privacy_unavailable")
            snapshot = await self.resolver.resolve(principal, request)
            snapshot.validate_request(request)
        except ExtractionBoundaryError:
            raise
        except ServiceFault:
            raise ExtractionBoundaryError("unauthorized_source") from None
        except Exception:
            raise ExtractionBoundaryError("source_changed") from None
        if self.backend is None:
            raise ExtractionBoundaryError("configuration_error")
        try:
            result = PageOutcome.model_validate(await self.backend.extract(request, snapshot))
            if result.request != request:
                raise ValueError("Outcome request differs from invocation")
            return snapshot.validate_outcome(result)
        except ExtractionBoundaryError:
            raise
        except Exception:
            raise ExtractionBoundaryError("provider_error") from None
