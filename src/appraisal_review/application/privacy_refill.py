"""Evidence-bound local display transformation; never advances business completion."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Literal

from appraisal_review.application.privacy_diagnostics import (
    diagnostic_stage,
    note_failure,
    restore_stage,
)
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.privacy_diagnostics import RestoreStage
from appraisal_review.domain.privacy_mapping import LocalMappingHandle, LocalMappingRecord
from appraisal_review.domain.privacy_models import PrivacyErrorCode, RehydrationPlan
from appraisal_review.domain.privacy_refill import (
    FinalLocalArtifact,
    FinalLocalManifest,
    PublishedRefillArtifact,
    PublishedRefillDescriptor,
    RefillTarget,
    verify_occurrences,
)
from appraisal_review.domain.privacy_scan import TextObservation
from appraisal_review.ports.privacy import (
    LocalSnapshotReader,
    PrivacyOutputOCR,
    RehydrationAuthority,
)
from appraisal_review.ports.privacy_refill import (
    PrivacyFinalSink,
    PrivacyMappingReader,
    PrivacyRefillProcessor,
    PrivacyRefillPublisher,
)


class RefillOCRFailure(PrivacyFault):
    """Bounded local diagnostics; never include text, identifiers, paths or PDF bytes."""

    def __init__(
        self,
        *,
        stage: Literal["published", "restored"],
        page: int,
        reason: Literal["ocr_unavailable", "ocr_deadline", "uncertain_ocr", "placeholder_mismatch"],
        observations: tuple[TextObservation, ...] = (),
    ) -> None:
        super().__init__(PrivacyErrorCode.VERIFICATION_FAILED)
        self.diagnostic: dict[str, object] = {
            "stage": stage,
            "page": page,
            "reason": reason,
            "observation_count": len(observations),
            "low_confidence_count": sum(
                o.confidence is None or o.confidence < 0.85 for o in observations
            ),
        }


def validate_refill(
    mapping: LocalMappingRecord, plan: RehydrationPlan, artifact: PublishedRefillArtifact
) -> None:
    mapping = LocalMappingRecord.model_validate(mapping)
    plan = RehydrationPlan.model_validate(plan)
    published = PublishedRefillDescriptor.model_validate(artifact.descriptor)
    source = mapping.command.source
    if not mapping.created_at <= datetime.now(UTC) < mapping.expires_at:
        raise ValueError("Expired mapping")
    if (plan.case_id, plan.document_id, plan.map_id) != (
        source.case_id,
        source.document_id,
        mapping.map_id,
    ):
        raise ValueError("Mapping differs")
    if (
        any(
            getattr(plan, name) != getattr(published, name)
            for name in (
                "case_id",
                "document_id",
                "run_id",
                "revision_id",
                "artifact_digest",
                "template_digest",
                "pages",
            )
        )
        or published.base_sanitized_digest != mapping.manifest.sanitized_digest
    ):
        raise ValueError("Publisher binding differs")
    if hashlib.sha256(artifact.pdf).hexdigest() != plan.artifact_digest:
        raise ValueError("Downloaded bytes differ")
    occurrences = {o.occurrence_id: o for o in mapping.manifest.occurrences}
    if set(occurrences) != {t.occurrence_id for t in published.targets} or set(occurrences) != {
        f.occurrence_id for f in plan.fields
    }:
        raise ValueError("Missing or extra occurrences")
    targets = {t.occurrence_id: t for t in published.targets}
    for field in plan.fields:
        target, occurrence = targets[field.occurrence_id], occurrences[field.occurrence_id]
        if (
            field.entity_id != occurrence.entity_id
            or target.entity_id != occurrence.entity_id
            or (field.output_field_id != target.output_field_id)
        ):
            raise ValueError("Unexpected entity or field")
        if field.operation == "omit":
            continue
        if not target.present or field.destination != target.region:
            raise ValueError("Unsupported destination or missing placeholder")


class LocalPrivacyRefillExecutor:
    def __init__(
        self,
        *,
        mappings: PrivacyMappingReader,
        sources: LocalSnapshotReader,
        publisher: PrivacyRefillPublisher,
        authority: RehydrationAuthority,
        processor: PrivacyRefillProcessor,
        ocr: PrivacyOutputOCR,
        sink: PrivacyFinalSink | None = None,
    ) -> None:
        self._mappings, self._sources = mappings, sources
        self._publisher, self._authority = publisher, authority
        self._processor, self._ocr, self._sink = processor, ocr, sink

    @diagnostic_stage(RestoreStage.OCR_VALIDATION)
    def _tokens(
        self,
        data: bytes,
        artifact: PublishedRefillArtifact,
        targets: tuple[RefillTarget, ...],
        *,
        stage: Literal["published", "restored"],
    ) -> None:
        pages = artifact.descriptor.pages
        with restore_stage(RestoreStage.RENDER):
            rendered = self._processor.render(data, pages)
        if len(rendered) != len(pages):
            raise ValueError("Page coverage differs")
        deadline = time.monotonic() + 30
        for image, page in zip(rendered, pages, strict=True):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RefillOCRFailure(stage=stage, page=page.number, reason="ocr_deadline")
            try:
                with restore_stage(RestoreStage.OCR):
                    observations = tuple(
                        TextObservation.model_validate(o)
                        for o in self._ocr.read(image.preview, page, timeout=remaining)
                    )
            except Exception:
                raise RefillOCRFailure(
                    stage=stage, page=page.number, reason="ocr_unavailable"
                ) from None
            if time.monotonic() > deadline:
                raise RefillOCRFailure(
                    stage=stage,
                    page=page.number,
                    reason="ocr_deadline",
                    observations=observations,
                )
            if (
                not observations
                or len(observations) > 10000
                or (sum(len(o.text) for o in observations) > 100000)
                or any(
                    o.origin != "ocr"
                    or o.confidence is None
                    or o.confidence < 0.85
                    or o.region.page != page.number
                    for o in observations
                )
            ):
                raise RefillOCRFailure(
                    stage=stage,
                    page=page.number,
                    reason="uncertain_ocr",
                    observations=observations,
                )
            expected = tuple(t for t in targets if t.region.page == page.number)
            try:
                verify_occurrences(observations, expected, pages, require_all=True)
                verify_occurrences(image.native, expected, pages, require_all=False)
            except ValueError:
                raise RefillOCRFailure(
                    stage=stage,
                    page=page.number,
                    reason="placeholder_mismatch",
                    observations=observations,
                ) from None

    @diagnostic_stage(RestoreStage.AUTOMATIC_RESTORE)
    def execute(self, handle: LocalMappingHandle, plan: RehydrationPlan) -> FinalLocalArtifact:
        try:
            handle = LocalMappingHandle.model_validate(handle)
            plan = RehydrationPlan.model_validate(plan)
            with restore_stage(RestoreStage.AUTHORITY):
                if self._authority.permits(plan) is not True:
                    raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED)
            with restore_stage(RestoreStage.MAPPING):
                mapping = LocalMappingRecord.model_validate(self._mappings.read(handle))
            if (
                mapping.map_id != handle.map_id
                or mapping.command.source.case_id != handle.case_id
                or mapping.expires_at != handle.expires_at
            ):
                raise ValueError("Mapping reader returned different identity")
            with restore_stage(RestoreStage.PUBLICATION):
                artifact = self._publisher.current(plan)
            with restore_stage(RestoreStage.PLAN):
                validate_refill(mapping, plan, artifact)
            with restore_stage(RestoreStage.AUTHORITY):
                if self._publisher.permits(plan, artifact) is not True:
                    raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED)
            self._tokens(artifact.pdf, artifact, artifact.descriptor.targets, stage="published")
            with restore_stage(RestoreStage.SOURCE):
                original = self._sources.read(mapping.command.source)
            with restore_stage(RestoreStage.CANDIDATE_WRITE):
                final_pdf = self._processor.write(mapping, plan, artifact, original)
            self._tokens(final_pdf, artifact, (), stage="restored")
            # Revalidate mapping access/retention and both authorities after the slow work.
            with restore_stage(RestoreStage.MAPPING):
                if self._mappings.read(handle) != mapping:
                    raise ValueError("Local inputs changed")
            with restore_stage(RestoreStage.SOURCE):
                if self._sources.read(mapping.command.source) != original:
                    raise ValueError("Local inputs changed")
            with restore_stage(RestoreStage.PLAN):
                validate_refill(mapping, plan, artifact)
            with restore_stage(RestoreStage.AUTHORITY):
                if (
                    self._authority.permits(plan) is not True
                    or self._publisher.permits(plan, artifact) is not True
                ):
                    raise PrivacyFault(PrivacyErrorCode.UNAUTHORIZED)
            result = FinalLocalArtifact(
                FinalLocalManifest(
                    case_id=plan.case_id,
                    document_id=plan.document_id,
                    map_id=plan.map_id,
                    run_id=plan.run_id,
                    revision_id=plan.revision_id,
                    input_artifact_digest=plan.artifact_digest,
                    final_digest=hashlib.sha256(final_pdf).hexdigest(),
                    byte_size=len(final_pdf),
                    restored_fields=tuple(
                        f.output_field_id for f in plan.fields if f.operation != "omit"
                    ),
                    omitted_fields=tuple(
                        f.output_field_id for f in plan.fields if f.operation == "omit"
                    ),
                ),
                final_pdf,
            )
            if self._sink is not None:
                self._sink.save(result, mapping.command.source)
            return result
        except PrivacyFault:
            raise
        except Exception as error:
            note_failure(error)
            raise PrivacyFault(PrivacyErrorCode.VERIFICATION_FAILED) from None
