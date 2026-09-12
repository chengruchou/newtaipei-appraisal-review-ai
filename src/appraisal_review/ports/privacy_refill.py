"""Trusted local refill ports; never inject publisher/authority adapters from requests."""

from typing import Protocol

from appraisal_review.domain.privacy_mapping import LocalMappingHandle, LocalMappingRecord
from appraisal_review.domain.privacy_models import LocalSourceSnapshot, PrivacyPage, RehydrationPlan
from appraisal_review.domain.privacy_refill import (
    FinalLocalArtifact,
    PublishedRefillArtifact,
    RefillRenderedPage,
)


class PrivacyMappingReader(Protocol):
    def read(self, handle: LocalMappingHandle) -> LocalMappingRecord: ...


class PrivacyRefillPublisher(Protocol):
    def current(self, plan: RehydrationPlan) -> PublishedRefillArtifact:
        """Fetch authenticated current run/revision bytes; a supplied digest is not proof."""
        ...

    def permits(self, plan: RehydrationPlan, artifact: PublishedRefillArtifact) -> bool:
        """Recheck authenticated release ownership and current run/revision before output."""
        ...


class PrivacyRefillProcessor(Protocol):
    def render(
        self, data: bytes, pages: tuple[PrivacyPage, ...]
    ) -> tuple[RefillRenderedPage, ...]: ...

    def write(
        self,
        mapping: LocalMappingRecord,
        plan: RehydrationPlan,
        artifact: PublishedRefillArtifact,
        original: bytes,
    ) -> bytes:
        """Write and independently reopen/check a separate local PDF in bounded processing."""
        ...


class PrivacyFinalSink(Protocol):
    def save(self, artifact: FinalLocalArtifact, source: LocalSourceSnapshot) -> None:
        """Publish a new local file only; never overwrite originals/downloaded artifacts."""
        ...
