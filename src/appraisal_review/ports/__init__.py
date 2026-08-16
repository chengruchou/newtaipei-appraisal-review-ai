"""Provider-neutral interfaces used by application services."""

from appraisal_review.ports.explanation import ExplanationGenerator
from appraisal_review.ports.extraction import DocumentExtractor

__all__ = ["DocumentExtractor", "ExplanationGenerator"]
