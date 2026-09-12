"""Recorded batch handling scope, and the guarantee that no privacy gate sits inline.

The current batch of standard formula documents was cleared by a professional and needs
no local privacy processing. These tests pin the two halves of that: an exemption must be
an attributed, bounded record, and the review pipeline must keep having no privacy state
for anyone to accidentally re-introduce a wait on.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.document_manifest import InputManifest, InputSpec
from appraisal_review.domain.review_contracts import CaseIdentity
from appraisal_review.domain.service_contracts import ActionPrerequisite, WorkflowState
from appraisal_review.domain.source_handling import BatchHandling, SourceHandlingScope

CLEARED = SourceHandlingScope(
    privacy_processing="not_required",
    document_ids=("forms-001", "criteria-001"),
    decided_by="reviewing appraiser",
    decided_on=date(2026, 9, 12),
    rationale="Standard formula and criteria forms carry no personal data.",
)


def identity() -> CaseIdentity:
    return CaseIdentity(
        case_id="shulin-001",
        version="r1",
        district="Shulin",
        zone="residential",
        land_use_category="ordinary_residence",
        effective_date=date(2022, 9, 1),
    )


def manifest(handling: BatchHandling | None = None) -> InputManifest:
    documents = [
        InputSpec(
            path=f"/absolute/{name}.pdf",
            document_id=f"{name}-001",
            version="v1",
            role=name,
            expected_hash="0" * 64,
        )
        for name in ("forms", "criteria")
    ]
    if handling is None:
        return InputManifest(identity=identity(), documents=documents)
    return InputManifest(identity=identity(), documents=documents, handling=handling)


class TestRecordedExemption:
    def test_exemption_covers_only_the_documents_it_names(self) -> None:
        assert CLEARED.exempts("forms-001")
        assert CLEARED.exempts("criteria-001")
        assert not CLEARED.exempts("reference-001")

    @pytest.mark.parametrize(
        "missing",
        [
            {"document_ids": ()},
            {"decided_by": "   "},
            {"decided_on": None},
            {"rationale": ""},
        ],
    )
    def test_unattributed_exemption_is_refused(self, missing: dict[str, object]) -> None:
        # An exemption with no authority, date, reason or scope is just the pipeline
        # being switched off quietly; it must not be representable.
        with pytest.raises(ValidationError):
            SourceHandlingScope.model_validate({**CLEARED.model_dump(), **missing})

    def test_duplicate_document_ids_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            SourceHandlingScope.model_validate(
                {**CLEARED.model_dump(), "document_ids": ("forms-001", "forms-001")}
            )

    def test_required_processing_carries_no_attribution(self) -> None:
        assert SourceHandlingScope(privacy_processing="required").privacy_processing == "required"
        with pytest.raises(ValidationError):
            SourceHandlingScope(privacy_processing="required", document_ids=("forms-001",))

    def test_exemption_is_not_a_privacy_receipt(self) -> None:
        # It states that sanitization was never asked for, not that one ran and passed.
        payload = CLEARED.model_dump(mode="json")
        assert payload["privacy_processing"] == "not_required"
        assert payload["schema_version"] == "source-handling-v1"
        assert not any(
            key in payload for key in ("sanitized_digest", "manifest", "approval", "passed")
        )
        assert "privacy-v1" not in str(payload)


class TestBatchDefaults:
    def test_unrecorded_batch_still_requires_processing(self) -> None:
        # The whole point: this batch's clearance must not become everyone's clearance.
        assert manifest().privacy_required("forms-001") is True
        assert BatchHandling().privacy_required("anything") is True

    def test_recorded_batch_is_exempt_only_for_its_own_documents(self) -> None:
        batch = manifest(BatchHandling(scopes=(CLEARED,)))
        assert batch.privacy_required("forms-001") is False
        assert batch.privacy_required("criteria-001") is False
        assert batch.privacy_required("some-later-document") is True

    def test_exemption_cannot_name_a_document_outside_the_batch(self) -> None:
        outside = SourceHandlingScope.model_validate(
            {**CLEARED.model_dump(), "document_ids": ("forms-001", "not-in-this-batch")}
        )
        with pytest.raises(ValidationError):
            manifest(BatchHandling(scopes=(outside,)))

    def test_conflicting_records_for_one_document_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            BatchHandling(scopes=(CLEARED, CLEARED))


class TestNoInlinePrivacyGate:
    """Guards the decoupling the new flow depends on, so a later change cannot undo it."""

    def test_no_privacy_prerequisite_exists(self) -> None:
        assert not [p for p in ActionPrerequisite if "privacy" in p.value]

    def test_no_privacy_workflow_state_exists(self) -> None:
        # If a privacy state appears here, a job can wait on sanitization again and the
        # direct-import flow silently stalls instead of failing loudly.
        assert not [s for s in WorkflowState if "privacy" in s.value]
