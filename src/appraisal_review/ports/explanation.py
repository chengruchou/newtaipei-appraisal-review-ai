"""Reviewer-facing explanation port."""

from typing import Protocol

from appraisal_review.domain.models import CanonicalCase, Finding


class ExplanationGenerator(Protocol):
    async def explain(self, case: CanonicalCase, findings: list[Finding]) -> str:
        """Explain existing findings without changing their status."""
        ...
