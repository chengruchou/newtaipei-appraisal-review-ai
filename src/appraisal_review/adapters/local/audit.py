"""In-memory audit adapter for local workflows and tests."""

from appraisal_review.domain.factor_models import AuditEvent


class InMemoryAuditLogger:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)
