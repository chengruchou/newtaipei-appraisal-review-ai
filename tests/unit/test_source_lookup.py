"""Unit tests for source lookup contracts and the SSRF-safe source reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.runtime_sources import (
    FetchResult,
    FixedSourceDocument,
    FixedUrlCatalog,
    HostNotAllowlisted,
    NullSearchProvider,
    OversizeBodyRefused,
    PrivateAddressRefused,
    ReadTimedOut,
    RedirectRefused,
    RetrievedDocument,
    SafeSourceReader,
    SchemeRefused,
    SearchProvider,
    SearchResult,
    SearchUnavailable,
)
from appraisal_review.application.source_lookup import (
    LOOKUP_TOOL_CONFIG,
    BudgetExceeded,
    FieldCandidate,
    LookupBudget,
    LookupOutcome,
    ModelRequestThrottle,
    SourceLookupJob,
    run_lookup,
)


def make_job(**overrides: object) -> SourceLookupJob:
    payload: dict[str, object] = {
        "operation_id": "op-1",
        "idempotency_key": "idem-1",
        "case_id": "case-1",
        "revision_id": "rev-1",
        "field_keys": ("land_area",),
    }
    payload.update(overrides)
    return SourceLookupJob.model_validate(payload)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_reader(
    responses: dict[str, FetchResult],
    *,
    addresses: dict[str, list[str]] | None = None,
    allowed_hosts: tuple[str, ...] = ("example.gov.tw",),
    clock: FakeClock | None = None,
    fetch_cost_seconds: float = 0.0,
    **kwargs: object,
) -> SafeSourceReader:
    resolved = addresses or {}
    active_clock = clock or FakeClock()

    def fetch(url: str) -> FetchResult:
        active_clock.advance(fetch_cost_seconds)
        return responses[url]

    def resolve(host: str) -> list[str]:
        return resolved.get(host, ["93.184.216.34"])

    return SafeSourceReader(
        allowed_hosts=allowed_hosts,
        fetch=fetch,
        resolve=resolve,
        clock=active_clock,
        **kwargs,  # type: ignore[arg-type]
    )


class TestBudget:
    def test_model_turn_cap_marks_exhausted(self) -> None:
        job = make_job(model_turns_used=3)
        with pytest.raises(BudgetExceeded) as exc:
            job.enforce_budget(elapsed_seconds=1.0)
        assert exc.value.dimension == "model_turns"
        assert job.stage == "exhausted"

    def test_document_cap_marks_exhausted(self) -> None:
        job = make_job(documents_read=6)
        with pytest.raises(BudgetExceeded) as exc:
            job.enforce_budget(elapsed_seconds=1.0)
        assert exc.value.dimension == "documents"
        assert job.stage == "exhausted"

    def test_time_cap_marks_exhausted(self) -> None:
        job = make_job()
        with pytest.raises(BudgetExceeded) as exc:
            job.enforce_budget(elapsed_seconds=90.0)
        assert exc.value.dimension == "total_seconds"
        assert job.stage == "exhausted"

    def test_within_budget_keeps_stage(self) -> None:
        job = make_job(model_turns_used=2, documents_read=5)
        job.enforce_budget(elapsed_seconds=89.9)
        assert job.stage == "queued"

    def test_default_caps(self) -> None:
        budget = LookupBudget()
        assert budget.max_model_turns == 3
        assert budget.max_documents == 6
        assert budget.max_total_seconds == 90.0


class TestSafeSourceReader:
    def test_reads_allowlisted_document(self) -> None:
        url = "https://example.gov.tw/land"
        reader = make_reader({url: FetchResult(status_code=200, body=b"area 120.5")})
        document = reader.read(url)
        assert document.text == "area 120.5"
        assert document.url == url
        assert document.truncated is False

    def test_refuses_non_https_scheme(self) -> None:
        reader = make_reader({})
        with pytest.raises(SchemeRefused):
            reader.read("http://example.gov.tw/land")

    def test_refuses_non_allowlisted_host(self) -> None:
        reader = make_reader({})
        with pytest.raises(HostNotAllowlisted):
            reader.read("https://evil.example.com/land")

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.1.2.3",
            "172.16.0.9",
            "192.168.1.5",
            "169.254.169.254",
            "::1",
            "fc00::1",
            "fe80::1",
        ],
    )
    def test_refuses_private_loopback_metadata_addresses(self, address: str) -> None:
        reader = make_reader({}, addresses={"example.gov.tw": [address]})
        with pytest.raises(PrivateAddressRefused):
            reader.read("https://example.gov.tw/land")

    def test_refuses_when_any_resolved_address_is_private(self) -> None:
        reader = make_reader({}, addresses={"example.gov.tw": ["93.184.216.34", "10.0.0.1"]})
        with pytest.raises(PrivateAddressRefused):
            reader.read("https://example.gov.tw/land")

    def test_refuses_redirect_to_refused_target(self) -> None:
        url = "https://example.gov.tw/start"
        reader = make_reader(
            {url: FetchResult(status_code=302, redirect_to="https://internal.local/steal")}
        )
        with pytest.raises(RedirectRefused):
            reader.read(url)

    def test_follows_redirect_to_allowlisted_target(self) -> None:
        reader = make_reader(
            {
                "https://example.gov.tw/a": FetchResult(
                    status_code=302, redirect_to="https://example.gov.tw/b"
                ),
                "https://example.gov.tw/b": FetchResult(status_code=200, body=b"final"),
            }
        )
        assert reader.read("https://example.gov.tw/a").text == "final"

    def test_refuses_redirect_loop_beyond_cap(self) -> None:
        reader = make_reader(
            {
                "https://example.gov.tw/a": FetchResult(
                    status_code=302, redirect_to="https://example.gov.tw/a"
                )
            }
        )
        with pytest.raises(RedirectRefused):
            reader.read("https://example.gov.tw/a")

    def test_refuses_oversize_body(self) -> None:
        url = "https://example.gov.tw/big"
        reader = make_reader({url: FetchResult(status_code=200, body=b"x" * 11)}, max_bytes=10)
        with pytest.raises(OversizeBodyRefused):
            reader.read(url)

    def test_refuses_when_wall_time_exceeded(self) -> None:
        clock = FakeClock()
        url = "https://example.gov.tw/slow"
        reader = make_reader(
            {url: FetchResult(status_code=200, body=b"late")},
            clock=clock,
            fetch_cost_seconds=25.0,
            max_seconds=20.0,
        )
        with pytest.raises(ReadTimedOut):
            reader.read(url)

    def test_truncates_to_max_chars(self) -> None:
        url = "https://example.gov.tw/long"
        reader = make_reader({url: FetchResult(status_code=200, body=b"abcdef")})
        document = reader.read(url, max_chars=4)
        assert document.text == "abcd"
        assert document.truncated is True


class TestSearchProviders:
    def test_null_provider_reports_unavailable(self) -> None:
        provider: SearchProvider = NullSearchProvider()
        job = make_job()
        try:
            provider.search("land value shulin", "moi-price-registry")
        except SearchUnavailable:
            job.stage = "search_unavailable"
        assert job.stage == "search_unavailable"

    def test_fixed_url_catalog_is_not_a_search(self) -> None:
        catalog = FixedUrlCatalog(
            [
                FixedSourceDocument(
                    result_id="doc-1",
                    source_id="moi-price-registry",
                    title="Registry portal",
                    url="https://example.gov.tw/registry",
                )
            ]
        )
        assert not hasattr(catalog, "search")
        assert catalog.documents_for("moi-price-registry")[0].result_id == "doc-1"
        assert catalog.documents_for("unknown") == ()


class TestFieldCandidate:
    def make_payload(self) -> dict[str, object]:
        return {
            "case_id": "case-1",
            "revision_id": "rev-1",
            "subject": "parcel",
            "field_key": "land_area",
            "value": "120.5",
            "unit": "m2",
            "source_ref": "doc-1",
            "source_location": "table 2 row 3",
            "retrieval_time": datetime(2026, 9, 12, 8, 0, tzinfo=UTC),
        }

    def test_default_status_is_candidate(self) -> None:
        candidate = FieldCandidate.model_validate(self.make_payload())
        assert candidate.status == "candidate"

    @pytest.mark.parametrize("status", ["verified", "adopted", "approved"])
    def test_no_verified_or_adopted_status_exists(self, status: str) -> None:
        payload = self.make_payload()
        payload["status"] = status
        with pytest.raises(ValidationError):
            FieldCandidate.model_validate(payload)


class TestToolDeclarations:
    def test_tool_config_matches_converse_shape(self) -> None:
        names = [tool["toolSpec"]["name"] for tool in LOOKUP_TOOL_CONFIG["tools"]]
        assert names == ["search_official_sources", "read_source"]
        for tool in LOOKUP_TOOL_CONFIG["tools"]:
            schema = tool["toolSpec"]["inputSchema"]["json"]
            assert schema["additionalProperties"] is False
        read_schema = LOOKUP_TOOL_CONFIG["tools"][1]["toolSpec"]["inputSchema"]["json"]
        assert read_schema["required"] == ["result_id"]
        assert "url" not in read_schema["properties"]


class TestModelRequestThrottle:
    def test_enforces_minimum_spacing(self) -> None:
        clock = FakeClock()
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock.advance(seconds)

        throttle = ModelRequestThrottle(1.2, clock=clock, sleep=sleep)
        throttle.acquire()
        assert sleeps == []
        throttle.acquire()
        assert sleeps == [pytest.approx(1.2)]
        clock.advance(0.5)
        throttle.acquire()
        assert sleeps[-1] == pytest.approx(0.7)

    def test_no_sleep_when_interval_already_elapsed(self) -> None:
        clock = FakeClock()
        sleeps: list[float] = []
        throttle = ModelRequestThrottle(1.2, clock=clock, sleep=sleeps.append)
        throttle.acquire()
        clock.advance(5.0)
        throttle.acquire()
        assert sleeps == []

    def test_rejects_non_positive_interval(self) -> None:
        with pytest.raises(ValueError):
            ModelRequestThrottle(0.0, clock=FakeClock(), sleep=lambda _s: None)


def assistant_tool_use(
    tool_use_id: str, name: str, tool_input: dict[str, object]
) -> dict[str, object]:
    return {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": tool_use_id, "name": name, "input": tool_input}}
                ],
            }
        },
    }


def assistant_text(text: str) -> dict[str, object]:
    return {
        "stopReason": "end_turn",
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
    }


class ScriptedModel:
    def __init__(
        self,
        responses: list[dict[str, object]],
        *,
        clock: FakeClock | None = None,
        cost_seconds: float = 0.0,
    ) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.seen_messages: list[list[dict[str, object]]] = []
        self.timeouts: list[float | None] = []
        self.clock = clock
        self.cost_seconds = cost_seconds

    def converse(
        self,
        *,
        messages: list[dict[str, object]],
        tool_config: dict[str, object],
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        assert tool_config is LOOKUP_TOOL_CONFIG
        self.calls += 1
        self.seen_messages.append([dict(m) for m in messages])
        self.timeouts.append(timeout_seconds)
        if self.clock is not None:
            self.clock.advance(self.cost_seconds)
        return self.responses.pop(0)


class FakeSearchProvider:
    def __init__(self, hits: dict[str, list[tuple[str, str]]]) -> None:
        self.hits = hits
        self.timeouts: list[float | None] = []

    def search(
        self, query: str, source_id: str, *, timeout_seconds: float | None = None
    ) -> list[SearchResult]:
        self.timeouts.append(timeout_seconds)
        return [
            SearchResult(result_id=rid, source_id=source_id, title=rid, url=url)
            for rid, url in self.hits.get(source_id, [])
        ]


class CountingReader:
    """ReaderPort fake that records every initiated read and its timeout."""

    def __init__(
        self,
        text: str = "area 120.5 m2",
        *,
        clock: FakeClock | None = None,
        cost_seconds: float = 0.0,
    ) -> None:
        self.text = text
        self.calls: list[tuple[str, float | None]] = []
        self.clock = clock
        self.cost_seconds = cost_seconds

    def read(
        self, url: str, *, max_chars: int | None = None, max_seconds: float | None = None
    ) -> RetrievedDocument:
        self.calls.append((url, max_seconds))
        if self.clock is not None:
            self.clock.advance(self.cost_seconds)
        return RetrievedDocument(url=url, text=self.text, truncated=False)


def assistant_multi_tool_use(calls: list[tuple[str, str, dict[str, object]]]) -> dict[str, object]:
    return {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": tid, "name": name, "input": tool_input}}
                    for tid, name, tool_input in calls
                ],
            }
        },
    }


CANDIDATE_JSON = (
    '[{"subject": "parcel", "field_key": "land_area", "value": "120.5", "unit": "m2",'
    ' "source_ref": "res-1", "source_location": "table 2",'
    ' "retrieval_time": "2026-09-12T08:00:00Z", "case_id": "spoofed", "revision_id": "spoofed"}]'
)


class TestRunLookup:
    def build(
        self, responses: list[dict[str, object]]
    ) -> tuple[SourceLookupJob, ScriptedModel, FakeSearchProvider, SafeSourceReader]:
        job = SourceLookupJob.model_validate(
            {
                "operation_id": "op-1",
                "idempotency_key": "idem-1",
                "case_id": "case-1",
                "revision_id": "rev-1",
                "field_keys": ("land_area",),
            }
        )
        model = ScriptedModel(responses)
        provider = FakeSearchProvider({"moi-registry": [("res-1", "https://example.gov.tw/doc1")]})
        reader = make_reader(
            {"https://example.gov.tw/doc1": FetchResult(status_code=200, body=b"area 120.5 m2")}
        )
        return job, model, provider, reader

    def test_full_loop_produces_cited_candidate(self) -> None:
        job, model, provider, reader = self.build(
            [
                assistant_tool_use(
                    "t1",
                    "search_official_sources",
                    {"query": "land area", "source_id": "moi-registry"},
                ),
                assistant_tool_use("t2", "read_source", {"result_id": "res-1"}),
                assistant_text(CANDIDATE_JSON),
            ]
        )
        outcome = run_lookup(
            job, model, provider, reader, FakeClock(), source_catalog={"moi-registry"}
        )
        assert outcome.stage == "candidates_ready"
        assert job.stage == "candidates_ready"
        assert len(outcome.candidates) == 1
        candidate = outcome.candidates[0]
        assert candidate.status == "candidate"
        assert candidate.source_ref == "res-1"
        assert candidate.case_id == "case-1"  # server-bound, spoof ignored
        assert candidate.revision_id == "rev-1"
        assert job.documents_read == 1
        assert [c.outcome for c in job.tool_calls] == ["ok", "ok"]

    def test_invalid_result_id_is_refused(self) -> None:
        job, model, provider, reader = self.build(
            [
                assistant_tool_use("t1", "read_source", {"result_id": "never-produced"}),
                assistant_text("[]"),
            ]
        )
        outcome = run_lookup(
            job, model, provider, reader, FakeClock(), source_catalog={"moi-registry"}
        )
        assert outcome.candidates == ()
        assert job.documents_read == 0
        assert job.tool_calls[0].outcome == "refused"
        feedback_message = cast("dict[str, Any]", model.seen_messages[1][-1])
        error_feedback = feedback_message["content"][0]["toolResult"]
        assert error_feedback["status"] == "error"

    def test_candidate_citing_unread_document_is_rejected(self) -> None:
        job, model, provider, reader = self.build([assistant_text(CANDIDATE_JSON)])
        outcome = run_lookup(
            job, model, provider, reader, FakeClock(), source_catalog={"moi-registry"}
        )
        assert outcome.stage == "candidates_ready"
        assert outcome.candidates[0].status == "rejected"

    def test_unknown_source_id_is_refused(self) -> None:
        job, model, provider, reader = self.build(
            [
                assistant_tool_use(
                    "t1", "search_official_sources", {"query": "q", "source_id": "not-catalogued"}
                ),
                assistant_text("[]"),
            ]
        )
        run_lookup(job, model, provider, reader, FakeClock(), source_catalog={"moi-registry"})
        assert job.tool_calls[0].outcome == "refused"

    def test_null_search_provider_ends_search_unavailable(self) -> None:
        job, model, _provider, reader = self.build(
            [
                assistant_tool_use(
                    "t1", "search_official_sources", {"query": "q", "source_id": "moi-registry"}
                ),
            ]
        )
        outcome = run_lookup(
            job,
            model,
            NullSearchProvider(),
            reader,
            FakeClock(),
            source_catalog={"moi-registry"},
        )
        assert outcome.stage == "search_unavailable"
        assert job.stage == "search_unavailable"
        assert outcome.candidates == ()

    def test_turn_budget_ends_exhausted(self) -> None:
        responses = [
            assistant_tool_use("t1", "read_source", {"result_id": "nope"}) for _ in range(4)
        ]
        job, model, provider, reader = self.build(responses)
        outcome = run_lookup(
            job, model, provider, reader, FakeClock(), source_catalog={"moi-registry"}
        )
        assert outcome.stage == "exhausted"
        assert job.stage == "exhausted"
        assert model.calls == 3

    def test_idempotent_retry_returns_existing_without_model_call(self) -> None:
        job, model, provider, reader = self.build([])
        existing = LookupOutcome(stage="candidates_ready", candidates=())
        prior: dict[str, LookupOutcome] = {"idem-1": existing}
        outcome = run_lookup(
            job,
            model,
            provider,
            reader,
            FakeClock(),
            source_catalog={"moi-registry"},
            prior_outcomes=prior,
        )
        assert outcome is existing
        assert model.calls == 0

    def test_unparseable_final_payload_fails_closed(self) -> None:
        job, model, provider, reader = self.build([assistant_text("the area is 120.5")])
        outcome = run_lookup(
            job, model, provider, reader, FakeClock(), source_catalog={"moi-registry"}
        )
        assert outcome.stage == "failed"
        assert outcome.candidates == ()


SEVEN_HITS = [(f"res-{i}", f"https://example.gov.tw/doc{i}") for i in range(1, 8)]


class TestActionBoundaryBudgets:
    """R5/P2: budgets must bind BEFORE each action starts and when results land."""

    def test_seventh_read_is_never_initiated_but_wrapup_still_allowed(self) -> None:
        job = make_job()
        model = ScriptedModel(
            [
                assistant_tool_use(
                    "t1", "search_official_sources", {"query": "q", "source_id": "moi-registry"}
                ),
                assistant_multi_tool_use(
                    [
                        (f"t{i + 1}", "read_source", {"result_id": rid})
                        for i, (rid, _url) in enumerate(SEVEN_HITS)
                    ]
                ),
                assistant_text(CANDIDATE_JSON),
            ]
        )
        provider = FakeSearchProvider({"moi-registry": SEVEN_HITS})
        reader = CountingReader()
        outcome = run_lookup(
            job, model, provider, reader, FakeClock(), source_catalog={"moi-registry"}
        )
        # The 7th read is refused at the start boundary: never initiated.
        assert len(reader.calls) == 6
        assert job.documents_read == 6
        outcomes = [c.outcome for c in job.tool_calls]
        assert outcomes == ["ok"] + ["ok"] * 6 + ["refused"]
        # The refusal is reported back to the model as an error toolResult.
        feedback = cast("dict[str, Any]", model.seen_messages[2][-1])
        seventh = feedback["content"][6]["toolResult"]
        assert seventh["status"] == "error"
        assert "budget" in json.dumps(seventh["content"])
        # After 6 lawful reads the model may still produce a final wrap-up.
        assert outcome.stage == "candidates_ready"
        assert outcome.candidates[0].status == "candidate"

    def test_model_response_landing_after_deadline_is_never_candidates_ready(self) -> None:
        clock = FakeClock()
        job = make_job()
        model = ScriptedModel([assistant_text(CANDIDATE_JSON)], clock=clock, cost_seconds=100.0)
        provider = FakeSearchProvider({})
        reader = CountingReader()
        outcome = run_lookup(job, model, provider, reader, clock, source_catalog={"moi-registry"})
        assert outcome.stage == "exhausted"
        assert job.stage == "exhausted"
        assert outcome.candidates == ()

    def test_tool_result_landing_after_deadline_ends_exhausted(self) -> None:
        clock = FakeClock()
        job = make_job()
        model = ScriptedModel(
            [
                assistant_tool_use(
                    "t1", "search_official_sources", {"query": "q", "source_id": "moi-registry"}
                ),
                assistant_tool_use("t2", "read_source", {"result_id": "res-1"}),
                assistant_text(CANDIDATE_JSON),
            ]
        )
        provider = FakeSearchProvider({"moi-registry": SEVEN_HITS[:1]})
        reader = CountingReader(clock=clock, cost_seconds=95.0)
        outcome = run_lookup(job, model, provider, reader, clock, source_catalog={"moi-registry"})
        assert outcome.stage == "exhausted"
        assert outcome.candidates == ()

    def test_transports_receive_remaining_budget_as_timeout(self) -> None:
        clock = FakeClock()
        job = make_job()
        model = ScriptedModel(
            [
                assistant_tool_use(
                    "t1", "search_official_sources", {"query": "q", "source_id": "moi-registry"}
                ),
                assistant_tool_use("t2", "read_source", {"result_id": "res-1"}),
                assistant_text("[]"),
            ],
            clock=clock,
            cost_seconds=10.0,
        )
        provider = FakeSearchProvider({"moi-registry": SEVEN_HITS[:1]})
        reader = CountingReader()
        outcome = run_lookup(job, model, provider, reader, clock, source_catalog={"moi-registry"})
        assert outcome.stage == "candidates_ready"
        # Model turn 1 starts at t=0 with the full 90s budget.
        assert model.timeouts[0] == pytest.approx(90.0)
        # Search starts at t=10 (one model turn consumed): 80s remain.
        assert provider.timeouts == [pytest.approx(80.0)]
        # Read starts at t=20 (two model turns consumed): 70s remain.
        assert reader.calls[0][1] == pytest.approx(70.0)

    def test_throttle_wait_consumes_the_job_clock(self) -> None:
        clock = FakeClock()
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock.advance(seconds)

        throttle = ModelRequestThrottle(1.2, clock=clock, sleep=sleep)
        job = make_job(budget={"max_total_seconds": 1.0})
        model = ScriptedModel(
            [
                assistant_tool_use("t1", "read_source", {"result_id": "nope"}),
                assistant_text("[]"),
            ]
        )
        provider = FakeSearchProvider({})
        reader = CountingReader()
        outcome = run_lookup(
            job,
            model,
            provider,
            reader,
            clock,
            source_catalog={"moi-registry"},
            throttle=throttle,
        )
        # The 1.2s throttle wait before turn 2 crosses the 1.0s job budget,
        # so the second model call is never started.
        assert sleeps == [pytest.approx(1.2)]
        assert model.calls == 1
        assert outcome.stage == "exhausted"


class TestSafeReaderPerCallTimeout:
    def test_per_call_max_seconds_binds_tighter_than_default(self) -> None:
        clock = FakeClock()
        url = "https://example.gov.tw/slow"
        reader = make_reader(
            {url: FetchResult(status_code=200, body=b"late")},
            clock=clock,
            fetch_cost_seconds=10.0,
        )
        with pytest.raises(ReadTimedOut):
            reader.read(url, max_seconds=5.0)

    def test_per_call_max_seconds_cannot_loosen_the_default(self) -> None:
        clock = FakeClock()
        url = "https://example.gov.tw/slow"
        reader = make_reader(
            {url: FetchResult(status_code=200, body=b"late")},
            clock=clock,
            fetch_cost_seconds=25.0,
        )
        with pytest.raises(ReadTimedOut):
            reader.read(url, max_seconds=50.0)
