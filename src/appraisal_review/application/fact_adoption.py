"""Adopt confirmed fact candidates into a NEW snapshot version and case revision.

fact_candidates deliberately stops at "confirmed, awaiting adoption": a named
human accepted an exact fetched value, but no snapshot or official table has it
yet. This module is the missing adoption step. One atomic command takes a batch
of confirmed candidates and produces, all together or not at all:

- a new calculation snapshot: the current registered snapshot's entries plus one
  ``human_confirmed`` entry per adopted candidate (``trace`` cites the exact
  confirmation receipt), with each adopted key removed from ``gaps``, refreshed
  by an injected ``recompute`` hook so DERIVED keys are recomputed
  deterministically - this service never invents a number itself;
- a new case revision: the job's current run is advanced with compare-and-set
  semantics on the revision the caller pinned, so a concurrent change conflicts
  instead of being overwritten;
- adopted candidates: each batch member flips ``confirmed`` -> ``adopted`` in
  the same SQLite transaction, so ``confirmed_unadopted`` stops listing it;
- a durable AdoptionRecord with idempotent replay on (job, actor, key).

The batch is atomic: one refused candidate fails the whole command with the
reason and the refused candidate's id, and nothing - snapshot, revision, job or
candidate status - changes.

Integration contract (the composition root wires all of it):
- ``candidates``: the existing SQLiteCandidateStore (shared ReviewDatabase);
- ``snapshots``: the RegisteredSnapshots instance the export/approval planes
  already read from (it must offer ``read`` and ``register``);
- ``jobs``: the ReviewJobService (only ``status`` is used);
- ``store``: adapters.local.adoption_store.SQLiteAdoptionStore over the SAME
  SQLiteReviewStore that holds the jobs, so the revision advance commits in the
  one review_state transaction;
- the HTTP plane lives at ``app.state.fact_adoption`` (see
  api/routes/fact_adoption.py).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from appraisal_review.application.fact_candidates import (
    CandidateConfirmation,
    FactCandidate,
)
from appraisal_review.application.service_guards import Principal, ServiceFault
from appraisal_review.domain.calculation_snapshot import CalculationSnapshot, SnapshotEntry
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.job_contracts import TERMINAL_STATUSES, JobStatus, JobStatusView
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActorReference,
    OpaqueID,
    Permission,
    RevisionReference,
    ServiceErrorCode,
    ServiceModel,
)

CandidateRefusalReason = Literal[
    "unknown_or_foreign_candidate",
    "not_confirmed",
    "duplicate_field_key",
    "missing_accept_receipt",
]


class AdoptionRefusal(ServiceFault):
    """VALIDATION refusal that NAMES the refused candidate for the caller.

    The HTTP plane still answers with the sanitized ServiceProblem envelope
    (batch atomicity is the visible contract there: nothing reads as adopted);
    the named id exists for in-process callers, logs and tests.
    """

    def __init__(self, candidate_id: str, reason: CandidateRefusalReason) -> None:
        super().__init__(ServiceErrorCode.VALIDATION)
        self.candidate_id = candidate_id
        self.reason = reason


class AdoptFactsCommand(ServiceModel):
    """Untrusted body; the server mints every identifier and stamps actor and time."""

    idempotency_key: OpaqueID
    expected_revision: OpaqueID
    candidate_ids: tuple[OpaqueID, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_candidates(self) -> AdoptFactsCommand:
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("A batch cannot name the same candidate twice")
        return self

    def payload_digest(self) -> str:
        return content_digest(self)


class CandidateAdoptionOutcome(ServiceModel):
    """What one candidate became; ``confirmed_revision`` records the receipt lineage."""

    candidate_id: OpaqueID
    receipt_id: OpaqueID
    subject_id: str = Field(min_length=1, max_length=100)
    field_key: str = Field(min_length=1, max_length=200)
    adopted_value: str = Field(max_length=1000)
    adopted_unit: str | None = Field(default=None, min_length=1, max_length=50)
    confirmed_revision: OpaqueID
    outcome: Literal["adopted"] = "adopted"


class AdoptionRecord(ServiceModel):
    """Durable record of one atomic adoption of confirmed candidates."""

    adoption_id: OpaqueID
    job_id: UUID
    case_id: OpaqueID
    from_revision: OpaqueID
    to_revision: OpaqueID
    candidate_ids: tuple[OpaqueID, ...] = Field(min_length=1, max_length=50)
    receipt_ids: tuple[OpaqueID, ...] = Field(min_length=1, max_length=50)
    actor: ActorReference
    adopted_at: int = Field(ge=0, strict=True)
    snapshot_digest: Digest
    idempotency_key: OpaqueID

    @model_validator(mode="after")
    def named_human_adoption(self) -> AdoptionRecord:
        if self.actor.kind != "human":
            raise ValueError("An adoption record requires a named human actor")
        if self.from_revision == self.to_revision:
            raise ValueError("Adoption must advance to a new revision")
        if len(self.candidate_ids) != len(self.receipt_ids):
            raise ValueError("Each adopted candidate cites exactly one receipt")
        for values in (self.candidate_ids, self.receipt_ids):
            if len(set(values)) != len(values):
                raise ValueError("Adopted candidates and receipts must be unique")
        return self


class AdoptionResult(ServiceModel):
    """The response a successful or exactly replayed adoption returns."""

    record: AdoptionRecord
    new_revision: RevisionReference
    outcomes: tuple[CandidateAdoptionOutcome, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def coherent(self) -> AdoptionResult:
        if (
            self.new_revision.case_id != self.record.case_id
            or self.new_revision.revision_id != self.record.to_revision
        ):
            raise ValueError("The new revision must be the record's target revision")
        if tuple(outcome.candidate_id for outcome in self.outcomes) != self.record.candidate_ids:
            raise ValueError("Outcomes must mirror the record's candidates exactly")
        if tuple(outcome.receipt_id for outcome in self.outcomes) != self.record.receipt_ids:
            raise ValueError("Outcomes must mirror the record's receipts exactly")
        return self


class CandidateReader(Protocol):
    """The slice of the candidate store adoption needs (case-scoped reads only)."""

    def read(self, case_id: str, candidate_id: str) -> FactCandidate | None: ...


class AdoptionSnapshots(Protocol):
    """RegisteredSnapshots-shaped provider: exact read plus trusted registration."""

    def read(self, case_id: str, revision_id: str) -> CalculationSnapshot | None: ...

    def register(self, snapshot: CalculationSnapshot) -> str: ...


class JobStatusReader(Protocol):
    """ReviewJobService-shaped handle; only the authorized status view is used."""

    async def status(self, principal: Principal, job_id: UUID) -> JobStatusView: ...


class AdoptionStore(Protocol):
    """Durable adoption commits with exact idempotent replay on (job, actor, key).

    ``commit`` applies the candidate flips, the adoption record insert, the
    job's run/revision advance (CAS on ``record.from_revision``) and the
    snapshot registration in ONE review-store transaction; any failed condition
    raises ``ServiceFault(CONFLICT)`` and persists nothing.
    """

    def replay(
        self, *, job_id: UUID, actor_id: str, idempotency_key: str, payload_digest: str
    ) -> AdoptionResult | None: ...

    def accepted_receipt(self, case_id: str, candidate_id: str) -> CandidateConfirmation | None: ...

    async def commit(
        self,
        result: AdoptionResult,
        adopted: tuple[FactCandidate, ...],
        *,
        snapshot: CalculationSnapshot,
        new_run_id: UUID,
        payload_digest: str,
    ) -> tuple[AdoptionResult, bool]: ...


def _adoptable_value(text: str) -> str | Decimal:
    """Decimal-parse numeric accepted values; keep dates and labels as exact text."""
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    return number if number.is_finite() else text


class FactAdoptionService:
    def __init__(
        self,
        *,
        candidates: CandidateReader,
        snapshots: AdoptionSnapshots,
        jobs: JobStatusReader,
        store: AdoptionStore,
        recompute: Callable[[CalculationSnapshot], CalculationSnapshot] | None = None,
        clock: Callable[[], int] = lambda: int(time.time()),
        new_id: Callable[[], str] = lambda: str(uuid4()),
        new_run_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self.candidates = candidates
        self.snapshots = snapshots
        self.jobs = jobs
        self.store = store
        # Identity by default: with no engine wired, adopted inputs land verbatim and
        # derived keys stay as they were. The integrator injects the deterministic
        # recompute so derived entries refresh from the merged inputs.
        self.recompute = recompute if recompute is not None else lambda snapshot: snapshot
        self.clock = clock
        self.new_id = new_id
        self.new_run_id = new_run_id

    async def adopt(
        self, principal: Principal, job_id: UUID, command: AdoptFactsCommand
    ) -> AdoptionResult:
        command = AdoptFactsCommand.model_validate_json(command.model_dump_json())
        # The job service answers NOT_FOUND for other principals' jobs; adoption
        # additionally demands the material-correction authority of a named human,
        # because it changes what every later readiness/basis/export view reads.
        status = await self.jobs.status(principal, job_id)
        case_id = status.job.case_id
        principal.require(case_id, Permission.CORRECT)
        if principal.actor.kind != "human":
            raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
        replayed = self.store.replay(
            job_id=job_id,
            actor_id=principal.actor.actor_id,
            idempotency_key=command.idempotency_key,
            payload_digest=command.payload_digest(),
        )
        if replayed is not None:
            return replayed
        current = status.current_run
        if current is None or command.expected_revision != current.revision.revision_id:
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        if (
            status.job_status in TERMINAL_STATUSES
            or status.job_status == JobStatus.RUNNING
            or status.cancel_requested
            or status.open_task_ids
        ):
            # A terminal or running job, a pending cancel, or an open human task all
            # bind the current run; adoption never yanks a revision out from under
            # them. (Open tasks have their own commit_response revision path.)
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        base = self.snapshots.read(case_id, current.revision.revision_id)
        if base is None:
            # Adoption merges into the trusted registered snapshot; with none
            # registered there is nothing honest to merge into.
            raise ServiceFault(ServiceErrorCode.CONFLICT)
        picked = self._validated_candidates(case_id, command)
        to_revision = self.new_id()
        new_reference = RevisionReference(
            case_id=case_id,
            revision_id=to_revision,
            # The review material itself did not change - the adopted values live in
            # the calculation snapshot registered under the new revision - so the
            # material digest carries forward from the revision they were merged onto.
            material_digest=current.revision.material_digest,
        )
        merged, outcomes = self._merged(base, new_reference, picked)
        refreshed = self._recomputed(merged, tuple(outcome.field_key for outcome in outcomes))
        record = AdoptionRecord(
            adoption_id=self.new_id(),
            job_id=job_id,
            case_id=case_id,
            from_revision=current.revision.revision_id,
            to_revision=to_revision,
            candidate_ids=command.candidate_ids,
            receipt_ids=tuple(outcome.receipt_id for outcome in outcomes),
            actor=principal.actor,
            adopted_at=self.clock(),
            snapshot_digest=refreshed.digest(),
            idempotency_key=command.idempotency_key,
        )
        result = AdoptionResult(record=record, new_revision=new_reference, outcomes=outcomes)
        adopted = tuple(
            FactCandidate.model_validate(candidate.model_dump(mode="json") | {"status": "adopted"})
            for candidate, _ in picked
        )
        stored, _created = await self.store.commit(
            result,
            adopted,
            snapshot=refreshed,
            new_run_id=self.new_run_id(),
            payload_digest=command.payload_digest(),
        )
        return stored

    def _validated_candidates(
        self, case_id: str, command: AdoptFactsCommand
    ) -> tuple[tuple[FactCandidate, CandidateConfirmation], ...]:
        """Admit the whole batch or refuse it naming the first offending candidate."""
        picked: list[tuple[FactCandidate, CandidateConfirmation]] = []
        seen_fields: set[str] = set()
        for candidate_id in command.candidate_ids:
            candidate = self.candidates.read(case_id, candidate_id)
            if candidate is None:
                # A candidate of another case is indistinguishable from an unknown
                # one on purpose: reads are case-scoped.
                raise AdoptionRefusal(candidate_id, "unknown_or_foreign_candidate")
            if candidate.status != "confirmed":
                raise AdoptionRefusal(candidate_id, "not_confirmed")
            if candidate.field_key in seen_fields:
                raise AdoptionRefusal(candidate_id, "duplicate_field_key")
            receipt = self.store.accepted_receipt(case_id, candidate_id)
            if receipt is None:
                raise AdoptionRefusal(candidate_id, "missing_accept_receipt")
            seen_fields.add(candidate.field_key)
            picked.append((candidate, receipt))
        return tuple(picked)

    @staticmethod
    def _merged(
        base: CalculationSnapshot,
        new_reference: RevisionReference,
        picked: tuple[tuple[FactCandidate, CandidateConfirmation], ...],
    ) -> tuple[CalculationSnapshot, tuple[CandidateAdoptionOutcome, ...]]:
        data = base.model_dump(mode="json")
        data["revision"] = new_reference.model_dump(mode="json")
        entries = data["entries"]
        gaps = data["gaps"]
        outcomes: list[CandidateAdoptionOutcome] = []
        for candidate, receipt in picked:
            entry = SnapshotEntry(
                state="present",
                value=_adoptable_value(receipt.accepted_value),
                unit=receipt.accepted_unit,
                origin="human_confirmed",
                trace=f"receipt:{receipt.receipt_id}",
            )
            entries[candidate.field_key] = entry.model_dump(mode="json")
            gaps.pop(candidate.field_key, None)
            outcomes.append(
                CandidateAdoptionOutcome(
                    candidate_id=candidate.candidate_id,
                    receipt_id=receipt.receipt_id,
                    subject_id=candidate.subject_id,
                    field_key=candidate.field_key,
                    adopted_value=receipt.accepted_value,
                    adopted_unit=receipt.accepted_unit,
                    confirmed_revision=receipt.expected_revision,
                )
            )
        return CalculationSnapshot.model_validate(data), tuple(outcomes)

    def _recomputed(
        self, merged: CalculationSnapshot, adopted_keys: tuple[str, ...]
    ) -> CalculationSnapshot:
        """Run the injected recompute over a detached copy and refuse a rogue result.

        The hook exists to refresh DERIVED keys from the merged inputs. It must not
        move the snapshot to another revision and it must not rewrite the values a
        human just adopted; either would falsify what the receipts attest.
        """
        detached = CalculationSnapshot.model_validate_json(merged.model_dump_json())
        refreshed = CalculationSnapshot.model_validate_json(
            self.recompute(detached).model_dump_json()
        )
        if refreshed.revision != merged.revision:
            raise ServiceFault(ServiceErrorCode.EXECUTION)
        for key in adopted_keys:
            entry = refreshed.entries.get(key)
            if entry is None or entry.model_dump(mode="json") != merged.entries[key].model_dump(
                mode="json"
            ):
                raise ServiceFault(ServiceErrorCode.EXECUTION)
        return refreshed
