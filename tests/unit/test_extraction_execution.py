"""Offline execution budgets, late SDK completion, usage and complete PNG validation."""

import asyncio
import base64
import struct
import threading
import zlib
from unittest.mock import Mock

import pytest
from test_document_extraction import PNG
from test_extraction_contracts import payload, snapshot
from test_extraction_preflight import backend_setup, principal

from appraisal_review.adapters.aws.document_extraction import (
    ExtractionError,
)
from appraisal_review.adapters.aws.extraction_errors import provider_failure
from appraisal_review.adapters.aws.extraction_execution import execute
from appraisal_review.adapters.local.png_validation import validate_png
from appraisal_review.application.extraction import AuthorizedExtractionService
from appraisal_review.application.extraction_budget import ExtractionLedger
from appraisal_review.domain.extraction_contracts import ExecutionBudget, PageRequest
from appraisal_review.domain.extraction_models import PageProposal
from appraisal_review.ports.document_extraction import ExtractionBoundaryError


def limits(**changes):
    return ExecutionBudget.model_validate(
        payload("evaluation.json")["budget"]
        | {
            "max_calls": 3,
            "max_pages": 3,
            "max_attempts_per_page": 3,
            "max_output_tokens": 30,
        }
        | changes
    )


def ledger(**changes):
    result = ExtractionLedger(limits(**changes))
    result.begin_page("synthetic-run")
    return result


def response(input_tokens=3, output_tokens=2):
    return {
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": "{}"}]}},
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }


async def run(call, state, **options):
    return await execute(
        call,
        lambda value: PageProposal(),
        ledger=state,
        attempts=3,
        tokens=10,
        timeout=options.pop("timeout", 1),
        backoff_base=0.01,
        backoff_cap=0.02,
        jitter=lambda upper: 0,
        **options,
    )


def provider_error(code, **usage):
    error = RuntimeError("PRIVATE-PROVIDER-CANARY")
    error.response = {"Error": {"Code": code}, "usage": usage}
    return error


@pytest.mark.parametrize(
    "code,expected",
    [
        ("AccessDeniedException", "access_denied"),
        ("ExpiredTokenException", "access_denied"),
        ("InvalidSignatureException", "access_denied"),
        ("ValidationException", "configuration_error"),
        ("ResourceNotFoundException", "configuration_error"),
        ("ModelTimeoutException", "timeout"),
        ("UnknownError", "provider_error"),
    ],
)
def test_permanent_provider_failures_are_safe_and_not_retried(code, expected):
    call = Mock(side_effect=provider_error(code, inputTokens=3, outputTokens=1))
    result = asyncio.run(run(call, ledger()))
    assert result.code == expected and len(result.attempts) == 1
    assert result.attempts[0].input_tokens == 3
    assert "PRIVATE-PROVIDER-CANARY" not in repr(result)
    call.assert_called_once()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("NoCredentialsError", "configuration_error"),
        ("ProfileNotFound", "configuration_error"),
        ("ReadTimeoutError", "timeout"),
        ("ConnectTimeoutError", "timeout"),
        ("EndpointConnectionError", "timeout"),
        ("ConnectionClosedError", "timeout"),
    ],
)
def test_sdk_error_classes_have_explicit_safe_categories(name, expected):
    assert provider_failure(type(name, (Exception,), {})("PRIVATE-CANARY")) == expected


@pytest.mark.parametrize(
    "code",
    [
        "ThrottlingException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelNotReadyException",
    ],
)
def test_only_known_transient_failures_retry_and_all_usage_is_retained(code):
    call = Mock(side_effect=[provider_error(code, inputTokens=5, outputTokens=1), response()])
    state = ledger()
    result = asyncio.run(run(call, state))
    assert result.code is None and len(result.attempts) == 2
    assert [a.input_tokens for a in result.attempts] == [5, 3]
    assert state.calls == 2 and state.reserved_output_tokens == 3 and state.in_flight == 0


@pytest.mark.parametrize("value", [None, -1, True, "2"])
def test_unknown_or_invalid_usage_remains_null_and_reserves_full_output_cap(value):
    state = ledger(max_output_tokens=15)
    result = asyncio.run(run(Mock(return_value=response(value, value)), state))
    assert result.code is None
    assert result.attempts[0].input_tokens is result.attempts[0].output_tokens is None
    assert state.reserved_output_tokens == 10
    blocked = Mock()
    assert asyncio.run(run(blocked, state)).code == "budget_exhausted"
    blocked.assert_not_called()


def test_actual_output_over_reservation_stops_run_without_discarding_usage():
    state = ledger()
    result = asyncio.run(run(Mock(return_value=response(3, 11)), state))
    assert result.code == "budget_exhausted" and result.attempts[0].output_tokens == 11
    assert state.stopped


def test_total_calls_pages_and_exact_run_binding_are_enforced():
    state = ledger(max_calls=1, max_attempts_per_page=1, max_pages=1)
    asyncio.run(run(Mock(return_value=response()), state))
    blocked = Mock()
    assert asyncio.run(run(blocked, state)).code == "budget_exhausted"
    blocked.assert_not_called()
    with pytest.raises(ExtractionBoundaryError, match="budget_exhausted"):
        state.begin_page("synthetic-run")
    with pytest.raises(ExtractionBoundaryError, match="configuration_error"):
        state.begin_page("different-run")


async def wait_released(state):
    while state.in_flight:
        await asyncio.sleep(0.001)


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_cancellation_keeps_sdk_slot_until_actual_completion(cancel):
    entered, release = threading.Event(), threading.Event()
    state = ledger()

    def delayed():
        entered.set()
        assert release.wait(2)
        return response()

    call = Mock(side_effect=delayed)

    async def exercise():
        notes = []
        task = asyncio.create_task(
            run(call, state, timeout=0.03 if not cancel else 1, on_attempt=notes.append)
        )
        assert await asyncio.to_thread(entered.wait, 1)
        try:
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.code == "timeout" and result.attempts[0].completion == "unknown"
                assert result.attempts[0].input_tokens is None
            assert state.in_flight == 1 and state.stopped
            assert len(notes) == 1 and notes[0].completion == "unknown"
            blocked = Mock()
            assert (await run(blocked, state)).code == "budget_exhausted"
            blocked.assert_not_called()
        finally:
            release.set()
            await asyncio.wait_for(wait_released(state), 1)
        assert state.in_flight == 0 and state.reserved_output_tokens == 10

    asyncio.run(exercise())
    call.assert_called_once()


def test_concurrent_call_does_not_exceed_model_slot_budget():
    entered, release = threading.Event(), threading.Event()
    state = ledger()

    def delayed():
        entered.set()
        assert release.wait(2)
        return response()

    async def exercise():
        task = asyncio.create_task(run(delayed, state))
        assert await asyncio.to_thread(entered.wait, 1)
        try:
            blocked = Mock()
            assert (await run(blocked, state)).code == "budget_exhausted"
            blocked.assert_not_called()
            assert state.in_flight == 1
        finally:
            release.set()
        assert (await task).code is None
        assert not state.stopped

    asyncio.run(exercise())


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def test_jitter_backoff_and_total_deadline_use_injected_clock():
    clock = Clock()
    state = ExtractionLedger(limits(max_elapsed_seconds=1), clock=clock)
    state.begin_page("synthetic-run")
    call = Mock(side_effect=[provider_error("ThrottlingException"), response()])
    result = asyncio.run(
        execute(
            call,
            lambda _: PageProposal(),
            ledger=state,
            attempts=3,
            tokens=10,
            timeout=1,
            backoff_base=0.4,
            backoff_cap=0.5,
            clock=clock,
            sleep=clock.sleep,
            jitter=lambda upper: upper,
        )
    )
    assert result.code is None and result.elapsed_seconds == 0.4
    assert result.attempts[0].backoff_seconds == 0.4
    clock.now = 2
    blocked = Mock()
    assert asyncio.run(run(blocked, state, clock=clock)).code == "budget_exhausted"
    blocked.assert_not_called()


def test_backoff_cannot_start_when_it_exceeds_remaining_run_time():
    clock = Clock()
    state = ExtractionLedger(limits(max_elapsed_seconds=0.1), clock=clock)
    state.begin_page("synthetic-run")
    call = Mock(side_effect=provider_error("ThrottlingException"))
    result = asyncio.run(
        execute(
            call,
            lambda _: PageProposal(),
            ledger=state,
            attempts=3,
            tokens=10,
            timeout=1,
            backoff_base=0.4,
            backoff_cap=1,
            clock=clock,
            sleep=clock.sleep,
            jitter=lambda upper: upper,
        )
    )
    assert result.code == "budget_exhausted" and len(result.attempts) == 1
    assert result.attempts[0].failure == "throttled"
    call.assert_called_once()


@pytest.mark.parametrize(
    "stop,text,expected",
    [
        ("content_filtered", "{}", "refused"),
        ("max_tokens", "{}", "truncated_output"),
        ("end_turn", "{", "malformed_output"),
    ],
)
def test_public_failure_outcome_keeps_usage_and_located_handoff(stop, text, expected):
    from unittest.mock import AsyncMock

    backend, _, mapping, _ = backend_setup()
    answer = response(7, 8)
    answer["stopReason"] = stop
    answer["output"]["message"]["content"][0]["text"] = text
    mapping["bedrock-runtime", "us-east-1"].converse.return_value = answer
    resolver = AsyncMock(resolve=AsyncMock(return_value=snapshot()))
    outcome = asyncio.run(
        AuthorizedExtractionService(resolver=resolver, backend=backend).extract(
            principal(), PageRequest.model_validate(payload())
        )
    )
    assert outcome.failure == expected and outcome.proposal is None
    assert outcome.telemetry.attempts[0].input_tokens == 7
    assert outcome.telemetry.attempts[0].output_tokens == 8
    assert outcome.handoffs[0].locations[0].page == 1


def test_public_success_preserves_unknown_usage_and_prompt_identity():
    from appraisal_review.adapters.aws.document_extraction import PROMPT_DIGEST, PROMPT_VERSION

    backend, _, mapping, _ = backend_setup()
    mapping["bedrock-runtime", "us-east-1"].converse.return_value = response(None, None)
    outcome = asyncio.run(backend.extract(PageRequest.model_validate(payload()), snapshot()))
    assert outcome.status == "candidate" and outcome.telemetry.attempts[0].input_tokens is None
    assert outcome.telemetry.prompt_version == PROMPT_VERSION
    assert outcome.telemetry.prompt_digest == PROMPT_DIGEST


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(width=1, height=1, raster=b"\0\xff\xff", interlace=0):
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 4, 0, 0, interlace))
        + chunk(b"IDAT", zlib.compress(raster))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    "image",
    [
        PNG[:24],
        PNG[:-1],
        PNG + b"PRIVATE-CANARY",
        PNG[:-5] + b"wrong",
        png(interlace=1),
        png(width=8001),
        png(width=5000, height=5000),
        png(raster=b"\0" * 100000),
        png(raster=b"\5\xff\xff"),
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=="
        ),
    ],
)
def test_png_rejects_fake_header_bad_crc_truncation_bombs_and_invalid_raster(image):
    with pytest.raises((ValueError, zlib.error)):
        validate_png(image, max_bytes=3750000, max_width=8000, max_height=8000, max_pixels=16000000)


def test_selected_image_limits_reject_before_any_provider_metadata_or_model_call():
    backend, clients, _, renderer = backend_setup()
    renderer.render.return_value = png(width=2, raster=b"\0\xff\xff\xff\xff")
    backend.policy = backend.policy.model_copy(update={"max_image_width": 1})
    backend.config = backend.config.model_copy(update={"max_image_width": 1})
    result = asyncio.run(backend.extract(PageRequest.model_validate(payload()), snapshot()))
    assert result.failure == "unsupported_input" and result.telemetry.attempts == ()
    clients.client.assert_not_called()


def test_legacy_result_never_converts_unknown_usage_to_zero():
    from test_document_extraction import setup

    client, extractor, source, _ = setup()
    client.converse.return_value = response(None, None)
    with pytest.raises(ExtractionError, match="usage_unavailable"):
        asyncio.run(extractor._extract_page(source, 1, PNG))


def test_attempt_hook_records_usage_before_backoff_and_failures_do_not_retry():
    notes = []
    clock = Clock()
    state = ExtractionLedger(limits(), clock=clock)
    state.begin_page("synthetic-run")

    async def sleep(seconds):
        assert len(notes) == 1 and notes[0].input_tokens == 5
        clock.now += seconds

    call = Mock(side_effect=[provider_error("ThrottlingException", inputTokens=5), response()])
    result = asyncio.run(
        execute(
            call,
            lambda _: PageProposal(),
            ledger=state,
            attempts=3,
            tokens=10,
            timeout=1,
            backoff_base=0.1,
            backoff_cap=1,
            clock=clock,
            sleep=sleep,
            jitter=lambda upper: upper,
            on_attempt=notes.append,
        )
    )
    assert len(notes) == 2 and notes[0].backoff_seconds == 0
    assert result.attempts[0].backoff_seconds == 0.1
    failed_sink = Mock(side_effect=RuntimeError("PRIVATE-SINK-CANARY"))
    call = Mock(return_value=response())
    state = ledger()
    result = asyncio.run(run(call, state, on_attempt=failed_sink))
    assert result.code == "provider_error" and state.stopped
    assert "PRIVATE-SINK-CANARY" not in repr(result)
    assert result.attempts[0].input_tokens == 3
    call.assert_called_once()


def test_png_text_metadata_and_bad_density_are_not_transmitted():
    for extra in (chunk(b"tEXt", b"name\0PRIVATE-CANARY"), chunk(b"pHYs", b"bad")):
        image = PNG[:33] + extra + PNG[33:]
        with pytest.raises(ValueError):
            validate_png(
                image, max_bytes=3750000, max_width=8000, max_height=8000, max_pixels=16000000
            )


def test_renderer_output_decodes_under_the_pinned_image_contract():
    import pymupdf

    from appraisal_review.adapters.local.snapshot_renderer import _render

    with pymupdf.open() as pdf:
        pdf.new_page(width=100, height=100).insert_text((10, 20), "Synthetic")
        image = _render(pdf.tobytes(), 1, 1, 100, 100)
    validate_png(image, max_bytes=3750000, max_width=8000, max_height=8000, max_pixels=16000000)


def test_completed_response_after_global_deadline_retains_measured_usage():
    clock = Clock()
    state = ExtractionLedger(limits(max_elapsed_seconds=1), clock=clock)
    state.begin_page("synthetic-run")

    def late():
        clock.now = 2
        return response()

    result = asyncio.run(run(late, state, clock=clock))
    assert result.code == "budget_exhausted" and result.proposal is None
    assert result.attempts[0].output_tokens == 2


def test_oversized_model_json_fails_without_losing_usage_or_retrying():
    backend, _, mapping, _ = backend_setup()
    backend.config = backend.config.model_copy(update={"max_response_characters": 8})
    answer = response()
    answer["output"]["message"]["content"][0]["text"] = " " * 9
    call = mapping["bedrock-runtime", "us-east-1"].converse
    call.return_value = answer
    outcome = asyncio.run(backend.extract(PageRequest.model_validate(payload()), snapshot()))
    assert (
        outcome.failure == "malformed_output" and outcome.telemetry.attempts[0].output_tokens == 2
    )
    call.assert_called_once()
