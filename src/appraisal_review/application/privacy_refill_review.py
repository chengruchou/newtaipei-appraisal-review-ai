"""Exact local staged OCR review; measured observations are never edited or discarded."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from appraisal_review.application.privacy_diagnostics import diagnostic_stage, restore_stage
from appraisal_review.application.privacy_refill import validate_refill
from appraisal_review.domain.privacy_diagnostics import (
    LocalRestoreFailure,
    RestoreFailureCode,
    RestoreStage,
)
from appraisal_review.domain.privacy_mapping import LocalMappingRecord
from appraisal_review.domain.privacy_models import (
    PrivacyRegion,
    RehydrationPlan,
    placeholder_text,
    validate_region,
)
from appraisal_review.domain.privacy_refill import (
    FinalLocalArtifact,
    FinalLocalManifest,
    PublishedRefillArtifact,
    overlaps,
    verify_occurrences,
    verify_text_regions,
)
from appraisal_review.domain.privacy_refill_review import (
    OCRReviewConfirmation,
    OCRReviewItem,
    OCRReviewObservation,
    OCRReviewPage,
    OCRReviewReceipt,
    OCRReviewView,
)
from appraisal_review.domain.privacy_scan import TextObservation
from appraisal_review.ports.privacy import PrivacyOutputOCR
from appraisal_review.ports.privacy_refill import PrivacyRefillProcessor


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class LocalRefillReview:
    """One principal, publication, original, plan, engine and unique output destination.

    The injected authority rechecks current publication, exact mapping/source and
    download bytes. Receipts are server-owned and session-local; restart loses them.
    """

    def __init__(
        self,
        *,
        principal_id: str,
        mapping: LocalMappingRecord,
        plan: RehydrationPlan,
        artifact: PublishedRefillArtifact,
        original: bytes,
        processor: PrivacyRefillProcessor,
        ocr: PrivacyOutputOCR,
        engine_identity: Callable[[], str],
        authorize: Callable[[], None],
        destination: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_retained_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        validate_refill(mapping, plan, artifact)
        if hashlib.sha256(original).hexdigest() != mapping.command.source.source_digest:
            raise ValueError("Original source differs")
        self.review_id = uuid4()
        self.mapping, self.plan, self.artifact, self.original = mapping, plan, artifact, original
        self.processor, self.ocr = processor, ocr
        self.engine_identity, self.authorize, self.now = engine_identity, authorize, now
        self.created_at = now()
        self.expires_at = min(mapping.expires_at, self.created_at + timedelta(minutes=15))
        self._deadline = time.monotonic() + (self.expires_at - self.created_at).total_seconds()
        if type(max_retained_bytes) is not int or not 0 < max_retained_bytes <= 64 * 1024 * 1024:
            raise ValueError("Bounded local review retention required")
        self.max_retained_bytes = max_retained_bytes
        with restore_stage(RestoreStage.ENGINE):
            self.engine_digest = engine_identity()
        if len(self.engine_digest) != 64 or any(
            c not in "0123456789abcdef" for c in self.engine_digest
        ):
            raise ValueError("Pinned OCR identity required")
        self.principal_id, self.destination = principal_id, destination
        self.binding_digest = _digest(
            {
                "review_id": str(self.review_id),
                "principal_id": principal_id,
                "mapping": mapping.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "artifact": artifact.descriptor.model_dump(mode="json"),
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "destination": destination,
                "expires_at": self.expires_at.isoformat(),
                "engine_digest": self.engine_digest,
            }
        )
        self._receipts: list[OCRReviewReceipt] = []
        self._images: dict[int, bytes] = {}
        self._seen: set[int] = set()
        self._candidate: bytes | None = None
        self._complete: FinalLocalArtifact | None = None
        self._revoked = False
        self._history: dict[str, OCRReviewView] = {}
        self._stage_observations: dict[str, tuple[TextObservation, ...]] = {}
        self._stage_images: dict[str, dict[int, bytes]] = {}
        self._measurement_digests: dict[str, str] = {}
        self._stage_page_digests: dict[str, tuple[str, ...]] = {}
        self._stage_view_bindings: dict[str, str] = {}
        self._stage_input_digests: dict[str, str] = {}
        self._check()
        self._prepare("published", artifact.pdf)

    def _check(self) -> None:
        self._lifetime()
        with restore_stage(RestoreStage.ENGINE):
            if self.engine_identity() != self.engine_digest:
                raise LocalRestoreFailure(
                    RestoreFailureCode.ENGINE_CHANGED, "Local OCR review expired or engine changed"
                )
        with restore_stage(RestoreStage.REVIEW_AUTHORITY):
            self.authorize()
        with restore_stage(RestoreStage.PLAN):
            validate_refill(self.mapping, self.plan, self.artifact)
        self._check_evidence()
        # Engine checks, publication reads and evidence hashing can outlive a
        # review lease. Never return authority based only on the entry check.
        self._lifetime()

    @diagnostic_stage(RestoreStage.REVIEW_EVIDENCE)
    def _check_evidence(self) -> None:
        for stage, measurements in self._stage_observations.items():
            if (
                _digest([o.model_dump(mode="json") for o in measurements])
                != self._measurement_digests[stage]
            ):
                raise LocalRestoreFailure(
                    RestoreFailureCode.EVIDENCE_CHANGED, "Retained stage measurements changed"
                )
            hashes = tuple(
                hashlib.sha256(png).hexdigest() for png in self._stage_images[stage].values()
            )
            if hashes != self._stage_page_digests[stage]:
                raise LocalRestoreFailure(
                    RestoreFailureCode.EVIDENCE_CHANGED, "Retained stage images changed"
                )
        for stage, view in self._history.items():
            if self._immutable_view(view) != self._stage_view_bindings[stage]:
                raise LocalRestoreFailure(
                    RestoreFailureCode.EVIDENCE_CHANGED, "Retained stage view changed"
                )
        for receipt in self._receipts:
            view = self._view if receipt.stage == self._view.stage else self._history[receipt.stage]
            if (
                receipt.principal_id != self.principal_id
                or receipt.binding_digest != self.binding_digest
                or receipt.engine_digest != self.engine_digest
                or receipt.input_sha256 != self._stage_input_digests[receipt.stage]
                or receipt.measurements_digest != self._measurement_digests[receipt.stage]
                or receipt.page_image_sha256 != self._stage_page_digests[receipt.stage]
                or receipt.readings != view.items
                or receipt.expires_at != self.expires_at
            ):
                raise LocalRestoreFailure(
                    RestoreFailureCode.EVIDENCE_CHANGED, "Local review receipt changed"
                )
        if hasattr(self, "_view"):
            if self._immutable_view(self._view) != self._view_binding:
                raise LocalRestoreFailure(
                    RestoreFailureCode.EVIDENCE_CHANGED, "Review measurements or scope changed"
                )
            for page in self._view.pages:
                if hashlib.sha256(self._images[page.number]).hexdigest() != page.image_sha256:
                    raise LocalRestoreFailure(
                        RestoreFailureCode.EVIDENCE_CHANGED, "Review image changed"
                    )
            if self._view.stage == "restored" and (
                self._candidate is None
                or hashlib.sha256(self._candidate).hexdigest() != self._view.input_sha256
            ):
                raise LocalRestoreFailure(
                    RestoreFailureCode.EVIDENCE_CHANGED, "Reviewed candidate changed"
                )
        self._admit(0)

    @diagnostic_stage(RestoreStage.REVIEW_LIFETIME)
    def _lifetime(self) -> None:
        if self._revoked:
            raise LocalRestoreFailure(RestoreFailureCode.REVIEW_REVOKED, "Local OCR review expired")
        if (
            not self.created_at <= self.now() < self.expires_at
            or time.monotonic() >= self._deadline
        ):
            raise LocalRestoreFailure(RestoreFailureCode.REVIEW_EXPIRED, "Local OCR review expired")

    @diagnostic_stage(RestoreStage.REVIEW)
    def archive_evidence(self) -> tuple[dict[str, object], dict[str, bytes]]:
        """Retain expired evidence without renewing or importing its authority."""
        if self._revoked or self._complete is not None:
            raise ValueError("Review cannot restart")
        self.authorize()
        self._check_evidence()
        views = {**self._history, self._view.stage: self._view}
        evidence: dict[str, object] = {
            "review_id": str(self.review_id),
            "principal_id": self.principal_id,
            "binding_digest": self.binding_digest,
            "engine_digest": self.engine_digest,
            "original_sha256": hashlib.sha256(self.original).hexdigest(),
            "publication_sha256": hashlib.sha256(self.artifact.pdf).hexdigest(),
            "destination": self.destination,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "authority": "revoked_after_archive",
            "views": {stage: view.model_dump(mode="json") for stage, view in views.items()},
            "observations": {
                stage: [o.model_dump(mode="json") for o in observations]
                for stage, observations in self._stage_observations.items()
            },
            "receipts": [r.model_dump(mode="json") for r in self._receipts],
        }
        images = {
            f"{stage}-page-{page}.png": png
            for stage, pages in self._stage_images.items()
            for page, png in pages.items()
        }
        self.authorize()
        return evidence, images

    def revoke_after_archive(self) -> None:
        if self._complete is not None:
            raise ValueError("Completed review cannot restart")
        self.authorize()
        self._revoked = True

    @property
    def retained_bytes(self) -> int:
        size = len(self.original) + len(self.artifact.pdf)
        if self._candidate is not None:
            size += len(self._candidate)
        size += sum(len(png) for images in self._stage_images.values() for png in images.values())
        size += sum(
            len(o.text.encode("utf-8"))
            for measurements in self._stage_observations.values()
            for o in measurements
        )
        views = dict(self._history)
        if hasattr(self, "_view"):
            views[self._view.stage] = self._view
        size += sum(
            len((i.expected_text or "").encode("utf-8"))
            + len((i.confirmed_reading or "").encode("utf-8"))
            for view in views.values()
            for i in view.items
        )
        return size

    def _admit(self, extra: int) -> None:
        if self.retained_bytes + extra > self.max_retained_bytes:
            raise ValueError("Local OCR review retention limit")

    @staticmethod
    def _immutable_view(view: OCRReviewView) -> str:
        value = view.model_dump(mode="json")
        value.pop("receipts")
        for item in value["items"]:
            item.pop("confirmed_reading")
        return _digest(value)

    def _prepare(self, stage: Literal["published", "restored"], pdf: bytes) -> None:
        self._check()
        pages = self.plan.pages
        with restore_stage(RestoreStage.RENDER):
            rendered = self.processor.render(pdf, pages)
        if len(rendered) != len(pages):
            raise ValueError("Page coverage differs")
        self._admit(sum(len(image.preview.png) for image in rendered))
        deadline = time.monotonic() + 30
        measured: list[TextObservation] = []
        image_records: list[OCRReviewPage] = []
        images = {}
        targets = self.artifact.descriptor.targets if stage == "published" else ()
        for page, image in zip(pages, rendered, strict=True):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("OCR deadline")
            with restore_stage(RestoreStage.OCR):
                observations = tuple(
                    TextObservation.model_validate(o)
                    for o in self.ocr.read(image.preview, page, timeout=remaining)
                )
            if (
                time.monotonic() > deadline
                or not observations
                or len(observations) > 10000
                or sum(len(o.text) for o in observations) > 100000
                or any(o.origin != "ocr" or o.region.page != page.number for o in observations)
            ):
                raise ValueError("Unavailable or unbounded OCR cannot be reviewed")
            for observation in observations:
                validate_region(observation.region, pages)
            # Native evidence is an independent non-overridable integrity check.
            verify_occurrences(
                image.native,
                tuple(t for t in targets if t.region.page == page.number),
                pages,
                require_all=False,
            )
            measured.extend(observations)
            if (
                len(measured) > 10000
                or sum(len(o.text.encode("utf-8")) for o in measured) > 1000000
            ):
                raise ValueError("Aggregate OCR evidence limit")
            images[page.number] = image.preview.png
            image_records.append(
                OCRReviewPage(
                    number=page.number,
                    width=page.width,
                    height=page.height,
                    image_sha256=hashlib.sha256(image.preview.png).hexdigest(),
                )
            )
        self._check()
        self._admit(
            sum(len(png) for png in images.values())
            + sum(len(o.text.encode("utf-8")) for o in measured)
            + sum(len(placeholder_text(t.entity_id).encode("utf-8")) for t in targets)
        )
        rows = [
            OCRReviewObservation(
                observation_id=uuid4(),
                page=o.region.page,
                bbox=o.region.bbox,
                raw_text=o.text,
                confidence=o.confidence,
            )
            for o in measured
        ]
        items: list[OCRReviewItem] = []
        # A complete human reading of the exact placeholder cell supersedes only
        # interpretation of that cell; every underlying measurement remains present.
        for target in targets:
            if not target.present:
                continue
            identifier = uuid4()
            covered = []
            for index, observation in enumerate(measured):
                if not overlaps(observation.region, target.region):
                    continue
                a, b = target.region.bbox, observation.region.bbox
                if not (a[0] <= b[0] < b[2] <= a[2] and a[1] <= b[1] < b[3] <= a[3]):
                    raise ValueError("Cross-cell OCR geometry cannot be overridden")
                if rows[index].review_item_id is not None:
                    raise ValueError("Ambiguous OCR cell")
                rows[index] = rows[index].model_copy(update={"review_item_id": identifier})
                covered.append(rows[index].observation_id)
            items.append(
                OCRReviewItem(
                    item_id=identifier,
                    kind="placeholder",
                    page=target.region.page,
                    bbox=target.region.bbox,
                    observation_ids=tuple(covered),
                    expected_text=placeholder_text(target.entity_id),
                )
            )
        for index, observation in enumerate(measured):
            if rows[index].review_item_id is not None:
                continue
            required = observation.confidence is None or observation.confidence < 0.85
            try:
                verify_occurrences((observation,), (), pages, require_all=False)
            except ValueError:
                required = True
            if required:
                identifier = uuid4()
                rows[index] = rows[index].model_copy(update={"review_item_id": identifier})
                items.append(
                    OCRReviewItem(
                        item_id=identifier,
                        kind="observation",
                        page=observation.region.page,
                        bbox=observation.region.bbox,
                        observation_ids=(rows[index].observation_id,),
                    )
                )
        measurements = [o.model_dump(mode="json") for o in measured]
        self._measurements_digest = _digest(measurements)
        review_digest = _digest(
            {
                "binding": self.binding_digest,
                "stage": stage,
                "input": hashlib.sha256(pdf).hexdigest(),
                "measurements": measurements,
                "pages": [p.model_dump(mode="json") for p in image_records],
                "items": [i.model_dump(mode="json") for i in items],
            }
        )
        self._view = OCRReviewView(
            review_id=self.review_id,
            stage=stage,
            review_digest=review_digest,
            engine_digest=self.engine_digest,
            input_sha256=hashlib.sha256(pdf).hexdigest(),
            pages=tuple(image_records),
            observations=tuple(rows),
            items=tuple(items),
            receipts=tuple(self._receipts),
        )
        self._images, self._seen = images, set()
        self._view_binding = self._immutable_view(self._view)
        self._stage_observations[stage] = tuple(measured)
        self._stage_images[stage] = images.copy()
        self._measurement_digests[stage] = self._measurements_digest
        self._stage_page_digests[stage] = tuple(p.image_sha256 for p in image_records)
        self._stage_view_bindings[stage] = self._view_binding
        self._stage_input_digests[stage] = self._view.input_sha256
        self._check()

    @diagnostic_stage(RestoreStage.REVIEW)
    def view(self) -> OCRReviewView:
        self._check()
        return self._view.model_copy(update={"receipts": tuple(self._receipts)})

    @diagnostic_stage(RestoreStage.REVIEW)
    def image(self, page: int) -> bytes:
        self._check()
        content = self._images[page]
        expected = next(p.image_sha256 for p in self._view.pages if p.number == page)
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Review image changed")
        self._seen.add(page)
        self._check()
        return content

    @diagnostic_stage(RestoreStage.REVIEW)
    def confirm(self, item_id: UUID, command: OCRReviewConfirmation) -> OCRReviewView:
        self._check()
        if command.review_digest != self._view.review_digest or self._complete is not None:
            raise ValueError("Stale OCR review command")
        item = next((i for i in self._view.items if i.item_id == item_id), None)
        if item is None:
            raise ValueError("Unknown local review item")
        page = next(p for p in self._view.pages if p.number == item.page)
        if item.page not in self._seen or command.page_image_sha256 != page.image_sha256:
            raise ValueError("Exact rendered page must be viewed")
        if not command.reading.strip() or any(ord(c) < 32 for c in command.reading):
            raise ValueError("Explicit visible reading required")
        if item.expected_text is not None and command.reading != item.expected_text:
            raise ValueError("Placeholder reading differs from exact bound target")
        if item.confirmed_reading is not None and command.reading != item.confirmed_reading:
            raise ValueError("Confirmed reading is immutable; restart review to change it")
        if item.confirmed_reading is None:
            self._admit(len(command.reading.encode("utf-8")))
        verify_text_regions(
            ((command.reading, PrivacyRegion(page=item.page, bbox=item.bbox)),),
            self.artifact.descriptor.targets if item.kind == "placeholder" else (),
            self.plan.pages,
            require_all=False,
        )
        replacement = item.model_copy(update={"confirmed_reading": command.reading})
        self._view = self._view.model_copy(
            update={
                "items": tuple(replacement if i.item_id == item_id else i for i in self._view.items)
            }
        )
        self._check()
        self._receipt()
        return self.view()

    @diagnostic_stage(RestoreStage.REVIEW)
    def history(self, stage: Literal["published", "restored"]) -> OCRReviewView:
        self._check()
        return self.view() if self._view.stage == stage else self._history[stage]

    def _receipt(self) -> bool:
        if any(i.confirmed_reading is None for i in self._view.items):
            return False
        self._check()
        readings = [
            (o.raw_text, PrivacyRegion(page=o.page, bbox=o.bbox))
            for o in self._view.observations
            if o.review_item_id is None
        ]
        readings.extend(
            (i.confirmed_reading, PrivacyRegion(page=i.page, bbox=i.bbox))
            for i in self._view.items
            if i.confirmed_reading is not None
        )
        verify_text_regions(
            tuple(readings),
            self.artifact.descriptor.targets if self._view.stage == "published" else (),
            self.plan.pages,
            require_all=True,
        )
        if not any(r.review_digest == self._view.review_digest for r in self._receipts):
            self._receipts.append(
                OCRReviewReceipt(
                    receipt_id=uuid4(),
                    review_id=self.review_id,
                    stage=self._view.stage,
                    principal_id=self.principal_id,
                    binding_digest=self.binding_digest,
                    review_digest=self._view.review_digest,
                    engine_digest=self.engine_digest,
                    input_sha256=self._view.input_sha256,
                    measurements_digest=self._measurements_digest,
                    page_image_sha256=tuple(p.image_sha256 for p in self._view.pages),
                    readings=self._view.items,
                    confirmed_at=self.now(),
                    expires_at=self.expires_at,
                )
            )
        return True

    @diagnostic_stage(RestoreStage.REVIEW)
    def advance(self) -> FinalLocalArtifact | None:
        self._check()
        if self._complete is not None:
            return self._complete
        if not self._receipt():
            return None
        if self._view.stage == "published":
            self._history["published"] = self.view()
            with restore_stage(RestoreStage.CANDIDATE_WRITE):
                candidate = self.processor.write(
                    self.mapping, self.plan, self.artifact, self.original
                )
            if type(candidate) is not bytes:
                raise ValueError("Immutable candidate PDF required")
            self._admit(len(candidate))
            self._candidate = candidate
            self._check()
            self._prepare("restored", self._candidate)
            if not self._receipt():
                return None
        if self._candidate is None:
            raise ValueError("Exact local candidate required")
        self._check()
        self._complete = FinalLocalArtifact(
            FinalLocalManifest(
                case_id=self.plan.case_id,
                document_id=self.plan.document_id,
                map_id=self.plan.map_id,
                run_id=self.plan.run_id,
                revision_id=self.plan.revision_id,
                input_artifact_digest=self.plan.artifact_digest,
                final_digest=hashlib.sha256(self._candidate).hexdigest(),
                byte_size=len(self._candidate),
                restored_fields=tuple(
                    f.output_field_id for f in self.plan.fields if f.operation != "omit"
                ),
                omitted_fields=tuple(
                    f.output_field_id for f in self.plan.fields if f.operation == "omit"
                ),
            ),
            self._candidate,
        )
        return self._complete

    @diagnostic_stage(RestoreStage.REVIEW)
    def permits_completed(self, digest: str) -> bool:
        self._check()
        return (
            self._complete is not None
            and self._complete.manifest.final_digest == digest
            and {r.stage for r in self._receipts} == {"published", "restored"}
            and all(r.expires_at == self.expires_at for r in self._receipts)
        )
