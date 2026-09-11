"""Immutable in-process material snapshots, not a durable revision repository."""

from dataclasses import dataclass

from pydantic import RootModel

from appraisal_review.domain.confidence import confirmation_digest
from appraisal_review.domain.factor_models import ReviewMaterial
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    MaterialRevision,
    RevisionReference,
    RuleReference,
    ValueRevision,
)


def source_preparation_revision(
    documents: tuple[DocumentReference, ...], revision_id: str
) -> MaterialRevision:
    """Capture authorized source identities before semantic rule/fact extraction.

    This is not review material or an approval. Assembly later captures a real
    ReviewMaterial child through RevisionSnapshot, preserving the source parent.
    """
    if not documents:
        raise ValueError("Preparation requires source documents")
    ordered = tuple(sorted(documents, key=lambda item: item.document_id))
    return MaterialRevision(
        reference=RevisionReference(
            case_id=ordered[0].case_id,
            revision_id=revision_id,
            material_digest=content_digest(RootModel[tuple[DocumentReference, ...]](ordered)),
        ),
        documents=ordered,
        rules=(),
        canonicalization="source-documents-json-v1",
    )


@dataclass(frozen=True)
class RevisionSnapshot:
    """Store only serialized immutable strings; each read creates detached models."""

    _material_json: str
    _revision_json: str

    @property
    def material(self) -> ReviewMaterial:
        return ReviewMaterial.model_validate_json(self._material_json)

    @property
    def revision(self) -> MaterialRevision:
        return MaterialRevision.model_validate_json(self._revision_json)

    @classmethod
    def capture(
        cls,
        material: ReviewMaterial,
        revision_id: str,
        *,
        parent: RevisionReference | None = None,
        changes: tuple[ValueRevision, ...] = (),
    ) -> "RevisionSnapshot":
        # A snapshot is internal data, not a grant of approval. Revalidate copies.
        detached = ReviewMaterial.model_validate_json(material.model_dump_json())
        if detached.policy.identity != detached.facts.identity:
            raise ValueError("Material identities differ")
        case_id = detached.policy.identity.case_id
        revision = MaterialRevision(
            reference=RevisionReference(
                case_id=case_id,
                revision_id=revision_id,
                material_digest=content_digest(detached),
            ),
            parent=parent,
            documents=tuple(
                DocumentReference(
                    case_id=case_id,
                    document_id=d.document_id,
                    version=d.version,
                    content_hash=d.content_hash,
                    purpose=d.role,
                )
                for d in sorted(detached.policy.registry.documents, key=lambda d: d.document_id)
            ),
            rules=tuple(
                RuleReference(
                    rule_set_id=r.rules.rule_set_id,
                    version=r.rules.version,
                    context=r.context,
                    content_hash=content_digest(r),
                )
                for r in sorted(detached.policy.rule_sets, key=lambda r: r.context.key())
            ),
            changes=changes,
        )
        return cls(detached.model_dump_json(), revision.model_dump_json())

    def revise(
        self, material: ReviewMaterial, revision_id: str, *, changes: tuple[ValueRevision, ...] = ()
    ) -> "RevisionSnapshot":
        """B calls after trusted correction; clear confirmations and require reapproval.

        This pure operation neither stores a revision nor confirms/approves it.
        The repository port must atomically check the expected parent on persistence.
        """
        candidate = ReviewMaterial.model_validate_json(material.model_dump_json())
        if candidate.policy.identity.case_id != self.revision.reference.case_id:
            raise ValueError("Cannot revise a different case")
        if revision_id == self.revision.reference.revision_id:
            raise ValueError("A revision cannot replace its parent")
        bundle = candidate.policy.rule_bundle
        if bundle is not None and bundle.identity == candidate.policy.identity:
            bundle.identity.version = revision_id
        candidate.policy.identity.version = candidate.facts.identity.version = revision_id
        previous = {(p.context.key(), p.pair.factor_id): p for p in self.material.facts.pairs}
        for pair in candidate.facts.pairs:
            old = previous.get((pair.context.key(), pair.pair.factor_id))
            for side in ("target", "comparable"):
                reliability = getattr(pair, f"{side}_reliability")
                old_reliability = getattr(old, f"{side}_reliability") if old is not None else None
                retain_native = (
                    old is not None
                    and old_reliability is not None
                    and old_reliability.method == reliability.method == "native_numeric"
                    and old_reliability.confirmation is None
                    and reliability.confirmation is None
                    and confirmation_digest(old, side) == confirmation_digest(pair, side)
                )
                if not retain_native:
                    # The side digest excludes method for confirmation. It cannot prove origin.
                    # Only unchanged native lineage survives; a label is not re-extraction.
                    if (
                        old is not None
                        and old_reliability is not None
                        and old_reliability.method
                        in {
                            "native_proposed",
                            "manual_proposed",
                        }
                    ):
                        # Local proposals never acquire native numeric eligibility. Retain
                        # their actual origin; a trusted revision is not a model execution.
                        reliability.method = (
                            old_reliability.method
                            if confirmation_digest(old, side) == confirmation_digest(pair, side)
                            else "manual_proposed"
                        )
                    else:
                        reliability.method = "model_proposed"
                reliability.confirmation = None
        return self.capture(candidate, revision_id, parent=self.revision.reference, changes=changes)
