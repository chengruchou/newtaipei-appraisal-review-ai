"""Trusted local export composition; never instantiate adapters from request data."""

from typing import Protocol

from appraisal_review.domain.privacy_export import PrivacyExportPayload


class PrivacyExportConfirmation(Protocol):
    def confirm(self, payload: PrivacyExportPayload) -> bool:
        """Present exact PDF and all reviewer text locally; require explicit human consent.

        Review unknown sensitive text as well as replacements. A generic yes flag
        or a prior source-only approval does not implement this trusted port.
        """
        ...


class PrivacyExportSink(Protocol):
    def accept(self, payload: PrivacyExportPayload) -> None:
        """Consume exactly these immutable bytes and allowlisted fields, no path lookup.

        Tests use a local sink only. A future network adapter needs separate
        authorization and integration acceptance; it must not add local context.
        """
        ...
