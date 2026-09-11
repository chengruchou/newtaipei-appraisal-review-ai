"""Explicit subject bindings and pure material changes after trusted admission."""

from dataclasses import dataclass
from typing import Literal

from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.domain.case_review import source_cell
from appraisal_review.domain.confidence import confirm_side, confirmation_digest
from appraisal_review.domain.document_models import SourceCitation
from appraisal_review.domain.factor_models import (
    EvidencedPair,
    FactorObservation,
    NormalizedValue,
    ReviewMaterial,
)
from appraisal_review.domain.models import EvidenceRef
from appraisal_review.domain.review_contracts import ComparisonContext, content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    FactSideReference,
    PublicValue,
    ValueRevision,
)
from appraisal_review.domain.source_purpose import SourcePurposes

Side = Literal["target", "comparable"]


@dataclass(frozen=True)
class MaterialSubject:
    """Application configuration, never a subject locator supplied by a response."""

    subject_id: str
    context: ComparisonContext
    factor_id: str
    side: Side


@dataclass(frozen=True, init=False)
class MaterialSubjectMap:
    """Keep serialized bindings so callers cannot mutate a nested context later.

    The human-task service must authenticate the principal, check the permission,
    and admit the exact task/revision before calling either mutation operation.
    These pure operations cannot persist tasks, approve material, or publish it.
    """

    _bindings: tuple[tuple[str, str, str, Side], ...]

    def __init__(self, subjects: tuple[MaterialSubject, ...]) -> None:
        bindings: list[tuple[str, str, str, Side]] = []
        for subject in subjects:
            if (
                not subject.subject_id
                or not subject.factor_id
                or subject.side
                not in {
                    "target",
                    "comparable",
                }
            ):
                raise ValueError("Invalid material subject binding")
            context = ComparisonContext.model_validate_json(subject.context.model_dump_json())
            bindings.append(
                (subject.subject_id, context.model_dump_json(), subject.factor_id, subject.side)
            )
        if len({binding[0] for binding in bindings}) != len(bindings):
            raise ValueError("Duplicate material subject binding")
        object.__setattr__(self, "_bindings", tuple(bindings))

    def _resolve(self, material: ReviewMaterial, subject_id: str) -> tuple[EvidencedPair, Side]:
        binding = next((b for b in self._bindings if b[0] == subject_id), None)
        if binding is None:
            raise ValueError("Unknown material subject")
        _, context_json, factor_id, side = binding
        context = ComparisonContext.model_validate_json(context_json)
        pairs = [
            pair
            for pair in material.facts.pairs
            if pair.context == context and pair.pair.factor_id == factor_id
        ]
        if len(pairs) != 1:
            raise ValueError("Material subject must resolve to exactly one factor side")
        return pairs[0], side

    @staticmethod
    def _project(pair: EvidencedPair, side: Side) -> PublicValue:
        observation: FactorObservation = getattr(pair.pair, side)
        return PublicValue(
            state="present" if observation.value is not None else "missing",
            value=observation.value,
            raw_text=observation.raw_text or "",
            unit=observation.value.unit if observation.value is not None else None,
            confidence=observation.confidence,
            evidence=tuple(getattr(pair, f"{side}_sources")),
        )

    def public_value(self, snapshot: RevisionSnapshot, subject_id: str) -> PublicValue:
        pair, side = self._resolve(snapshot.material, subject_id)
        return self._project(pair, side)

    def side_reference(self, snapshot: RevisionSnapshot, subject_id: str) -> FactSideReference:
        pair, side = self._resolve(snapshot.material, subject_id)
        return FactSideReference(
            context=pair.context,
            factor_id=pair.pair.factor_id,
            side=side,
            input_digest=confirmation_digest(pair, side),
        )

    @staticmethod
    def _human(actor: ActorReference) -> ActorReference:
        actor = ActorReference.model_validate_json(actor.model_dump_json())
        if actor.kind != "human":
            raise PermissionError("Material changes require a trusted human actor")
        return actor

    @staticmethod
    def _evidence(
        refs: tuple[SourceCitation, ...],
        observation: FactorObservation,
        purposes: SourcePurposes,
    ) -> list[EvidenceRef]:
        result: list[EvidenceRef] = []
        for ref in refs:
            existing = [
                item
                for item in observation.evidence
                if item.document_id == ref.document_id
                and item.source_file == purposes.forms.uri
                and item.page == ref.page
                and item.bounding_box == ref.bbox
                and item.coordinate_system == "pdf_bottom_left"
            ]
            # Preserve measured location scores verbatim. A newly supplied location
            # has no measured extraction score; confirmation must establish its use.
            scores = [item.confidence for item in existing] or [0.0]
            result.extend(
                EvidenceRef(
                    document_id=ref.document_id,
                    source_file=purposes.forms.uri,
                    page=ref.page,
                    block_ids=[ref.region_id],
                    confidence=score,
                    bounding_box=ref.bbox,
                    coordinate_system="pdf_bottom_left",
                )
                for score in scores
            )
        return result

    def correct(
        self,
        snapshot: RevisionSnapshot,
        correction: ValueRevision,
        actor: ActorReference,
        revision_id: str,
        *,
        allow_missing_evidence: bool = False,
    ) -> RevisionSnapshot:
        """Apply an admitted transcription/value correction without confirming it."""
        actor = self._human(actor)
        correction = ValueRevision.model_validate_json(correction.model_dump_json())
        if correction.corrected is not None or correction.corrected_by is not None:
            raise ValueError("Correction results must be authored by the application")
        material = snapshot.material
        pair, side = self._resolve(material, correction.subject_id)
        original = self._project(pair, side)
        if content_digest(correction.original) != content_digest(original):
            raise ValueError("Correction original differs from the current observation")
        proposed = correction.proposed
        if (
            proposed is None
            or proposed.state != "present"
            or not isinstance(proposed.value, NormalizedValue)
        ):
            raise ValueError("Correction requires a present normalized value")
        if proposed.unit != proposed.value.unit:
            raise ValueError("Correction units are inconsistent")
        if isinstance(original.value, NormalizedValue) and (
            proposed.value.type != original.value.type or proposed.unit != original.unit
        ):
            raise ValueError("Correction cannot reinterpret the original type or unit")
        if proposed.confidence != original.confidence:
            raise ValueError("Correction cannot change measured observation confidence")
        observation: FactorObservation = getattr(pair.pair, side)
        if not allow_missing_evidence and (
            original.value is None or not original.evidence or not observation.evidence
        ):
            raise ValueError("Missing values or evidence require an explicit supply task")
        purposes = SourcePurposes.selected(
            material.policy.registry, bundle=material.policy.rule_bundle
        )
        if not purposes.allows(list(proposed.evidence), "case"):
            raise ValueError("Correction evidence must resolve to the selected case forms")
        if not proposed.raw_text.strip() or not any(
            (bool(ref.excerpt) and proposed.raw_text in ref.excerpt)
            or (
                not ref.excerpt
                and any(
                    region.id == ref.region_id and region.kind in {"image", "cell"}
                    for region in purposes.forms.pages[ref.page - 1].regions
                )
            )
            for ref in proposed.evidence
        ):
            raise ValueError("Correction transcription requires supporting source text or image")
        if original.evidence and {source_cell(ref) for ref in original.evidence} != {
            source_cell(ref) for ref in proposed.evidence
        }:
            raise ValueError("Correction cannot change the observation source anchors")
        updated = FactorObservation(
            raw_text=proposed.raw_text,
            value=proposed.value,
            evidence=self._evidence(proposed.evidence, observation, purposes),
            confidence=observation.confidence,
        )
        setattr(pair.pair, side, updated)
        setattr(pair, f"{side}_sources", list(proposed.evidence))
        recorded = ValueRevision(
            subject_id=correction.subject_id,
            original=original,
            proposed=proposed,
            corrected=self._project(pair, side),
            corrected_by=actor,
        )
        return snapshot.revise(
            material, revision_id, changes=(*snapshot.revision.changes, recorded)
        )

    def confirm(
        self,
        snapshot: RevisionSnapshot,
        subject_id: str,
        actor: ActorReference,
        revision_id: str,
        *,
        retained_confirmations: tuple[FactSideReference, ...] = (),
    ) -> RevisionSnapshot:
        """Confirm one side; retain only explicitly authorized previous confirmations.

        The application derives the allowlist from admitted FACT response events,
        never from snapshot metadata. Entries must remain unchanged and belong to
        this human. Corrections still clear all previous confirmations via ``revise``.
        """
        actor = self._human(actor)
        self._resolve(snapshot.material, subject_id)
        revised = snapshot.revise(snapshot.material, revision_id, changes=snapshot.revision.changes)
        material = revised.material
        pair, side = self._resolve(material, subject_id)
        purposes = SourcePurposes.selected(
            material.policy.registry, bundle=material.policy.rule_bundle
        )
        if not purposes.allows(getattr(pair, f"{side}_sources"), "case"):
            raise ValueError("Confirmation evidence must resolve to the selected case forms")
        confirm_side(pair, side, reviewer=actor.actor_id)
        previous = snapshot.material
        for candidate in material.facts.pairs:
            old_pairs = [
                old
                for old in previous.facts.pairs
                if old.context == candidate.context
                and old.pair.factor_id == candidate.pair.factor_id
            ]
            if len(old_pairs) != 1:
                continue
            old = old_pairs[0]
            for other_side in ("target", "comparable"):
                if candidate is pair and other_side == side:
                    continue
                prior = getattr(old, f"{other_side}_reliability")
                confirmation = prior.confirmation
                if (
                    prior.method != "reviewer_confirmed"
                    or confirmation is None
                    or confirmation.reviewer != actor.actor_id
                    or confirmation.input_digest != confirmation_digest(old, other_side)
                    or confirmation.input_digest != confirmation_digest(candidate, other_side)
                    or FactSideReference(
                        context=old.context,
                        factor_id=old.pair.factor_id,
                        side=other_side,
                        input_digest=confirmation.input_digest,
                    )
                    not in retained_confirmations
                    or not purposes.allows(getattr(candidate, f"{other_side}_sources"), "case")
                ):
                    continue
                try:
                    confirm_side(candidate, other_side, reviewer=actor.actor_id)
                except ValueError:
                    # A prior assertion never resolves missing provenance or ambiguity.
                    continue
        return RevisionSnapshot.capture(
            material,
            revision_id,
            parent=snapshot.revision.reference,
            changes=snapshot.revision.changes,
        )
