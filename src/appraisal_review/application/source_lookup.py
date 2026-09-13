"""Contracts for model-driven source lookup jobs. NOT wired into the app.

Groundwork for letting the Bedrock Converse loop fill missing case data by
really searching and reading official sources. Nothing here is imported by
create_app or any composition module; candidates produced by a lookup are
never auto-adopted and never marked verified.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Collection, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal, Protocol

from pydantic import ConfigDict, Field, ValidationError

from appraisal_review.domain.document_models import DocumentModel
from appraisal_review.domain.service_contracts import ContractModel, OpaqueID


class SearchUnavailable(Exception):
    """No real search capability is configured; never fabricate results."""


class SourceReadRefused(Exception):
    """Base class for every typed refusal from a safe source reader."""


LookupStage = Literal[
    "queued",
    "model_turn",
    "searching",
    "reading",
    "candidates_ready",
    "search_unavailable",
    "failed",
    "exhausted",
]

BudgetDimension = Literal["model_turns", "documents", "total_seconds"]


class BudgetExceeded(Exception):
    """A lookup budget cap was hit; the job must stop, never silently continue."""

    def __init__(self, dimension: BudgetDimension) -> None:
        self.dimension: BudgetDimension = dimension
        super().__init__(f"lookup budget exceeded: {dimension}")


class LookupBudget(ContractModel):
    """Hard caps for one lookup job; enforcement raises, never trims silently."""

    max_model_turns: int = Field(default=3, ge=1)
    max_documents: int = Field(default=6, ge=1)
    max_total_seconds: float = Field(default=90.0, gt=0)
    max_document_chars: int = Field(default=20_000, ge=1)

    def check(self, *, model_turns: int, documents: int, elapsed_seconds: float) -> None:
        """Raise BudgetExceeded when any cap is reached or passed."""
        if model_turns >= self.max_model_turns:
            raise BudgetExceeded("model_turns")
        if documents >= self.max_documents:
            raise BudgetExceeded("documents")
        if elapsed_seconds >= self.max_total_seconds:
            raise BudgetExceeded("total_seconds")


class ToolCallRecord(ContractModel):
    """Audit entry for one tool call made during a lookup job."""

    tool_name: Literal["search_official_sources", "read_source"]
    params_digest: str = Field(min_length=1)
    outcome: Literal["ok", "refused", "error"]
    duration_ms: int = Field(ge=0)


class FieldCandidate(ContractModel):
    """A value the model proposes with citation. Never adopted automatically.

    There is intentionally no "verified" or "adopted" status: promotion into
    case data is a separate human-reviewed operation outside this module.
    Absence reasons are recorded as FieldAbsence, never encoded in `value`.
    """

    case_id: OpaqueID
    revision_id: OpaqueID
    subject: str = Field(min_length=1)
    field_key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    unit: str | None = None
    source_ref: str = Field(min_length=1)
    source_location: str = Field(min_length=1)
    applicable_date: date | None = None
    retrieval_time: datetime
    status: Literal["candidate", "rejected"] = "candidate"


class FieldAbsence(ContractModel):
    """Why a field could not be found; stored separately from any value."""

    case_id: OpaqueID
    revision_id: OpaqueID
    field_key: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SourceLookupJob(DocumentModel):
    """Mutable state of one lookup job; retries reuse the idempotency key."""

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, revalidate_instances="always", validate_assignment=True
    )

    operation_id: OpaqueID
    idempotency_key: OpaqueID
    case_id: OpaqueID
    revision_id: OpaqueID
    field_keys: tuple[str, ...] = Field(min_length=1)
    stage: LookupStage = "queued"
    budget: LookupBudget = LookupBudget()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    model_turns_used: int = Field(default=0, ge=0)
    documents_read: int = Field(default=0, ge=0)

    def enforce_budget(self, *, elapsed_seconds: float) -> None:
        """Check caps; on a hit, mark the job exhausted and re-raise."""
        try:
            self.budget.check(
                model_turns=self.model_turns_used,
                documents=self.documents_read,
                elapsed_seconds=elapsed_seconds,
            )
        except BudgetExceeded:
            self.stage = "exhausted"
            raise


# Converse toolSpec declarations, kept as data so the adapter that eventually
# hosts the loop passes them through unchanged. The server binds case,
# revision, and permissions from its own context; the model may only pick a
# catalogued source_id or a result_id produced earlier in the same job, never
# an arbitrary URL.
SEARCH_OFFICIAL_SOURCES_TOOL: Final[dict[str, Any]] = {
    "toolSpec": {
        "name": "search_official_sources",
        "description": (
            "Search one catalogued official source for documents relevant to a "
            "query. Returns result ids usable with read_source."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms."},
                    "source_id": {
                        "type": "string",
                        "description": "Identifier of a catalogued official source.",
                    },
                },
                "required": ["query", "source_id"],
                "additionalProperties": False,
            }
        },
    }
}

READ_SOURCE_TOOL: Final[dict[str, Any]] = {
    "toolSpec": {
        "name": "read_source",
        "description": (
            "Read the text of one search result previously returned in this "
            "job. Accepts only a result_id from search_official_sources."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "result_id": {
                        "type": "string",
                        "description": "A result id produced earlier in this job.",
                    }
                },
                "required": ["result_id"],
                "additionalProperties": False,
            }
        },
    }
}

LOOKUP_TOOL_CONFIG: Final[dict[str, Any]] = {
    "tools": [SEARCH_OFFICIAL_SOURCES_TOOL, READ_SOURCE_TOOL]
}


class ModelRequestThrottle:
    """Process-wide pacing gate for ALL model (Bedrock Converse) calls.

    The venue limit is below 1 request per second across the whole process,
    so one shared instance must gate every model call site; per-worker or
    per-job throttles do NOT satisfy the rule. Uses a monotonic clock and a
    lock so concurrent callers are serialized fairly.
    """

    def __init__(
        self,
        min_interval_seconds: float = 1.2,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be positive")
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed: float | None = None

    def acquire(self) -> None:
        """Block until at least min_interval_seconds since the previous call."""
        with self._lock:
            now = self._clock()
            if self._next_allowed is not None and now < self._next_allowed:
                self._sleep(self._next_allowed - now)
                now = self._clock()
            self._next_allowed = max(now, self._next_allowed or now) + self._min_interval


class SearchHit(Protocol):
    """Structural shape of one search result (see adapters.runtime_sources)."""

    @property
    def result_id(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def url(self) -> str: ...
    @property
    def snippet(self) -> str: ...


class SearchPort(Protocol):
    """Search over catalogued official sources; raises SearchUnavailable.

    timeout_seconds is the wall-clock budget remaining for this one call;
    the transport must not run longer than that.
    """

    def search(
        self, query: str, source_id: str, *, timeout_seconds: float | None = None
    ) -> Sequence[SearchHit]: ...


class ReadDocument(Protocol):
    @property
    def url(self) -> str: ...
    @property
    def text(self) -> str: ...


class ReaderPort(Protocol):
    """SSRF-guarded reader; raises SourceReadRefused subclasses.

    max_seconds, when given, tightens (never loosens) the reader's own
    per-call wall-clock cap for this one read.
    """

    def read(
        self, url: str, *, max_chars: int | None = None, max_seconds: float | None = None
    ) -> ReadDocument: ...


class ConverseClientPort(Protocol):
    """Minimal Bedrock Converse-shaped client; no boto3 import here.

    timeout_seconds is the wall-clock budget remaining for this one call;
    the transport must not wait longer than that for a response.
    """

    def converse(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_config: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LookupOutcome:
    """Terminal result of one lookup job run."""

    stage: LookupStage
    candidates: tuple[FieldCandidate, ...]
    absences: tuple[FieldAbsence, ...] = ()


def _params_digest(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record(
    job: SourceLookupJob,
    tool_name: Literal["search_official_sources", "read_source"],
    params: dict[str, Any],
    outcome: Literal["ok", "refused", "error"],
    duration_ms: int,
) -> None:
    entry = ToolCallRecord(
        tool_name=tool_name,
        params_digest=_params_digest(params),
        outcome=outcome,
        duration_ms=duration_ms,
    )
    job.tool_calls = (*job.tool_calls, entry)


def _tool_result(tool_use_id: str, payload: dict[str, Any], *, error: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "toolResult": {"toolUseId": tool_use_id, "content": [{"json": payload}]}
    }
    if error:
        result["toolResult"]["status"] = "error"
    return result


def _parse_candidates(
    job: SourceLookupJob, text: str, read_result_ids: frozenset[str]
) -> tuple[FieldCandidate, ...]:
    """Validate model-proposed candidates; citation must be a document read here.

    case_id and revision_id are always overwritten from the job (server
    binding); a candidate citing anything not actually read in this job is
    demoted to status "rejected", never silently trusted.
    """
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("candidate payload must be a JSON array")
    candidates: list[FieldCandidate] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("candidate entries must be objects")
        payload = dict(item)
        payload["case_id"] = job.case_id
        payload["revision_id"] = job.revision_id
        payload.pop("status", None)
        candidate = FieldCandidate.model_validate(payload)
        if candidate.source_ref not in read_result_ids:
            candidate = candidate.model_copy(update={"status": "rejected"})
        candidates.append(candidate)
    return tuple(candidates)


def run_lookup(
    job: SourceLookupJob,
    model_client: ConverseClientPort,
    search_provider: SearchPort,
    reader: ReaderPort,
    clock: Callable[[], float],
    *,
    source_catalog: Collection[str],
    prior_outcomes: MutableMapping[str, LookupOutcome] | None = None,
    throttle: ModelRequestThrottle | None = None,
) -> LookupOutcome:
    """Drive the Converse tool loop for one lookup job. NOT wired into the app.

    The model only ever picks a catalogued source_id or a result_id produced
    earlier in the same job; URLs come from the search provider and are
    fetched through the SSRF-guarded reader. Retries with the same
    idempotency key return the stored outcome without running again.
    """
    if prior_outcomes is not None and job.idempotency_key in prior_outcomes:
        return prior_outcomes[job.idempotency_key]

    def finish(outcome: LookupOutcome) -> LookupOutcome:
        job.stage = outcome.stage
        if prior_outcomes is not None:
            prior_outcomes[job.idempotency_key] = outcome
        return outcome

    started = clock()
    budget = job.budget

    def elapsed() -> float:
        return clock() - started

    def remaining() -> float:
        return budget.max_total_seconds - elapsed()

    def deadline_passed() -> bool:
        return elapsed() >= budget.max_total_seconds

    def exhausted() -> LookupOutcome:
        return finish(LookupOutcome(stage="exhausted", candidates=()))

    hits_by_id: dict[str, SearchHit] = {}
    read_ids: set[str] = set()
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "text": (
                        "Find candidate values for these missing fields using the "
                        "provided tools only: " + ", ".join(job.field_keys) + ". "
                        "Finish with a JSON array of candidate objects citing a "
                        "result_id you actually read."
                    )
                }
            ],
        }
    ]
    while True:
        # START boundary for a model turn: turn count and job deadline. The
        # document cap deliberately does NOT gate a model turn, so a final
        # wrap-up after the last lawful read remains possible.
        if job.model_turns_used >= budget.max_model_turns or deadline_passed():
            return exhausted()
        job.stage = "model_turn"
        if throttle is not None:
            throttle.acquire()
            # The throttle wait consumed the same job clock; re-check the
            # START boundary before actually issuing the model call.
            if deadline_passed():
                return exhausted()
        response = model_client.converse(
            messages=messages, tool_config=LOOKUP_TOOL_CONFIG, timeout_seconds=remaining()
        )
        job.model_turns_used += 1
        # ACCEPTANCE boundary: a model response that lands after the job
        # deadline must never turn into a successful outcome.
        if deadline_passed():
            return exhausted()
        message = response["output"]["message"]
        messages.append(message)
        tool_uses = [block["toolUse"] for block in message.get("content", []) if "toolUse" in block]
        if not tool_uses:
            text = "".join(block.get("text", "") for block in message.get("content", []))
            try:
                candidates = _parse_candidates(job, text, frozenset(read_ids))
            except (ValueError, ValidationError):
                return finish(LookupOutcome(stage="failed", candidates=()))
            return finish(LookupOutcome(stage="candidates_ready", candidates=candidates))
        feedback: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            name = tool_use["name"]
            params = dict(tool_use.get("input", {}))
            call_started = clock()

            def elapsed_ms(call_started: float = call_started) -> int:
                return max(0, int((clock() - call_started) * 1000))

            # START boundary for any tool action: after the job deadline no
            # new action may begin, and no later model turn could lawfully
            # accept its result either, so the job ends exhausted.
            if deadline_passed():
                return exhausted()
            if name == "search_official_sources":
                source_id = str(params.get("source_id", ""))
                if source_id not in source_catalog:
                    _record(job, name, params, "refused", elapsed_ms())
                    feedback.append(
                        _tool_result(
                            tool_use["toolUseId"],
                            {"error": "source_id is not in the catalog"},
                            error=True,
                        )
                    )
                    continue
                job.stage = "searching"
                try:
                    hits = search_provider.search(
                        str(params.get("query", "")), source_id, timeout_seconds=remaining()
                    )
                except SearchUnavailable:
                    _record(job, name, params, "refused", elapsed_ms())
                    return finish(LookupOutcome(stage="search_unavailable", candidates=()))
                _record(job, name, params, "ok", elapsed_ms())
                # ACCEPTANCE boundary: a result landing after the deadline
                # ends the job exhausted instead of feeding the model.
                if deadline_passed():
                    return exhausted()
                for hit in hits:
                    hits_by_id[hit.result_id] = hit
                feedback.append(
                    _tool_result(
                        tool_use["toolUseId"],
                        {
                            "results": [
                                {"result_id": h.result_id, "title": h.title, "snippet": h.snippet}
                                for h in hits
                            ]
                        },
                        error=False,
                    )
                )
            elif name == "read_source":
                result_id = str(params.get("result_id", ""))
                chosen = hits_by_id.get(result_id)
                if chosen is None:
                    _record(job, name, params, "refused", elapsed_ms())
                    feedback.append(
                        _tool_result(
                            tool_use["toolUseId"],
                            {"error": "result_id was not produced in this job"},
                            error=True,
                        )
                    )
                    continue
                # START boundary for the document dimension: a read past the
                # cap is never initiated. The refusal is reported back so
                # the model may still wrap up within its remaining turns.
                if job.documents_read >= budget.max_documents:
                    _record(job, name, params, "refused", elapsed_ms())
                    feedback.append(
                        _tool_result(
                            tool_use["toolUseId"],
                            {"error": "document budget exhausted; read not initiated"},
                            error=True,
                        )
                    )
                    continue
                job.stage = "reading"
                try:
                    document = reader.read(
                        chosen.url,
                        max_chars=budget.max_document_chars,
                        max_seconds=remaining(),
                    )
                except SourceReadRefused as refusal:
                    _record(job, name, params, "refused", elapsed_ms())
                    feedback.append(
                        _tool_result(
                            tool_use["toolUseId"],
                            {"error": f"source read refused: {type(refusal).__name__}"},
                            error=True,
                        )
                    )
                    continue
                job.documents_read += 1
                read_ids.add(result_id)
                _record(job, name, params, "ok", elapsed_ms())
                # ACCEPTANCE boundary: a document landing after the deadline
                # ends the job exhausted instead of feeding the model.
                if deadline_passed():
                    return exhausted()
                feedback.append(
                    _tool_result(
                        tool_use["toolUseId"],
                        {"result_id": result_id, "url": document.url, "text": document.text},
                        error=False,
                    )
                )
            else:
                feedback.append(
                    _tool_result(tool_use["toolUseId"], {"error": "unknown tool"}, error=True)
                )
        messages.append({"role": "user", "content": feedback})
