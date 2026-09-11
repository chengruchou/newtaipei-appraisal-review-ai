"""Physical SDK sends across models, threads/processes, retries and revoked authority."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from moto import mock_aws

from appraisal_review.adapters.aws.bedrock_dispatch import (
    install_bedrock_dispatch,
    require_bedrock_dispatch,
)
from appraisal_review.adapters.aws.dynamodb_model_dispatch import DynamoDBModelDispatchStore
from appraisal_review.adapters.local.sqlite_model_dispatch import SqliteModelDispatchStore
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.ports.model_dispatch import DispatchDenied, DispatchGuard, dispatch_guard


@contextmanager
def server(*, failures=0, disconnects=0):
    sends = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.handle_request()

        def do_GET(self):
            self.handle_request()

        def handle_request(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            sends.append((time.monotonic(), self.path))
            if len(sends) <= disconnects:
                self.close_connection = True
                return
            fail = len(sends) <= failures
            body = json.dumps(
                {"message": "Retry the synthetic request"}
                if fail
                else {
                    "inputTokens": 1,
                    "modelDetails": {},
                    "stopReason": "end_turn",
                    "output": {"message": {"role": "assistant", "content": [{"text": "{}"}]}},
                    "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                    "metrics": {"latencyMs": 0},
                }
            ).encode()
            self.send_response(429 if fail else 200)
            self.send_header("Content-Type", "application/json")
            if fail:
                self.send_header("x-amzn-errortype", "ThrottlingException")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", sends
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


class LocalSyntheticAdmission:
    """Test-only stand-in: explicitly recognizes this localhost fixture envelope."""

    def check(self, parts):
        assert len(parts) == 4
        assert parts[0].surface == "model_prompt"
        assert parts[1].surface == "metadata"
        assert parts[1].content.startswith(b"http://127.0.0.1:")
        operation = json.loads(parts[3].content)
        assert operation["service"] in {"bedrock", "bedrock-runtime"}
        assert operation["method"] in {"GET", "POST"}
        assert operation["url"].encode() == parts[1].content
        if parts[0].content:
            assert isinstance(json.loads(parts[0].content), dict)


def client(url, dispatcher, *, service="bedrock-runtime", attempts=1):
    sdk = boto3.client(
        service,
        region_name="us-east-1",
        endpoint_url=url,
        config=Config(
            signature_version=UNSIGNED,
            connect_timeout=1,
            read_timeout=1,
            retries={"mode": "standard", "total_max_attempts": attempts},
        ),
    )
    return install_bedrock_dispatch(
        sdk,
        dispatcher,
        competition_admission=LocalSyntheticAdmission(),
        allow_loopback_for_testing=True,
    )


def converse(sdk, model="synthetic-model"):
    return sdk.converse(
        modelId=model,
        messages=[{"role": "user", "content": [{"text": "x"}]}],
        inferenceConfig={"maxTokens": 1},
    )


def assert_spacing(sends, count):
    assert len(sends) == count
    assert all(b[0] - a[0] >= 1.1 for a, b in pairwise(sends))


def test_all_models_and_count_tokens_and_control_share_actual_send_gate(tmp_path):
    path = tmp_path / "dispatch.sqlite3"
    with server() as (url, sends):
        runtime = client(url, SharedModelDispatcher(SqliteModelDispatchStore(path)))
        control = client(
            url, SharedModelDispatcher(SqliteModelDispatchStore(path)), service="bedrock"
        )

        def invoke(operation):
            with dispatch_guard(DispatchGuard(time.monotonic() + 12)):
                if operation == "count":
                    return runtime.count_tokens(
                        modelId="model-c",
                        input={
                            "converse": {
                                "messages": [{"role": "user", "content": [{"text": "synthetic"}]}]
                            }
                        },
                    )
                if operation == "control":
                    return control.get_foundation_model(modelIdentifier="model-d")
                return converse(runtime, operation)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(invoke, ["model-a", "model-b", "count", "control"]))
        assert_spacing(sends, 4)
        assert any("count-tokens" in path for _, path in sends)
        runtime.close()
        control.close()


def test_actual_sdk_retry_is_throttled_and_charged(tmp_path):
    with server(failures=1) as (url, sends):
        sdk = client(
            url, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")), attempts=2
        )
        guard = DispatchGuard(time.monotonic() + 10, max_sends=2)
        with dispatch_guard(guard):
            converse(sdk)
        assert guard.sends == 2
        assert_spacing(sends, 2)
        sdk.close()


def test_sdk_implicit_retry_cannot_spend_unreserved_attempt(tmp_path):
    with server(failures=1) as (url, sends):
        sdk = client(
            url, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")), attempts=2
        )
        with (
            dispatch_guard(DispatchGuard(time.monotonic() + 10)),
            pytest.raises(DispatchDenied, match="attempt_budget"),
        ):
            converse(sdk)
        assert len(sends) == 1
        sdk.close()


def test_multi_process_and_completed_process_restart_retain_send_spacing(tmp_path):
    path = tmp_path / "dispatch.sqlite3"
    SqliteModelDispatchStore(path)
    source = """
import sys, time
from pathlib import Path
import boto3
from botocore import UNSIGNED
from botocore.config import Config
from appraisal_review.adapters.aws.bedrock_dispatch import install_bedrock_dispatch
from appraisal_review.adapters.local.sqlite_model_dispatch import SqliteModelDispatchStore
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.ports.model_dispatch import DispatchGuard, dispatch_guard
sdk = boto3.client("bedrock-runtime", region_name="us-east-1", endpoint_url=sys.argv[1],
    config=Config(signature_version=UNSIGNED, retries={"total_max_attempts": 1}))
class LocalSyntheticAdmission:
    def check(self, parts):
        assert parts[1].content.startswith(b"http://127.0.0.1:")
install_bedrock_dispatch(sdk, SharedModelDispatcher(SqliteModelDispatchStore(Path(sys.argv[2]))),
    competition_admission=LocalSyntheticAdmission(), allow_loopback_for_testing=True)
with dispatch_guard(DispatchGuard(time.monotonic() + 15)):
    sdk.converse(modelId=sys.argv[3], messages=[{"role":"user","content":[{"text":"x"}]}],
        inferenceConfig={"maxTokens": 1})
sdk.close()
"""
    with server() as (url, sends):
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", source, url, str(path), f"model-{i}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for i in range(3)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=25)
            assert process.returncode == 0, (stdout, stderr)
        restarted = subprocess.run(
            [sys.executable, "-c", source, url, str(path), "restart-model"],
            capture_output=True,
            timeout=20,
        )
        assert restarted.returncode == 0, restarted.stderr
        assert_spacing(sends, 4)


@pytest.mark.parametrize("reason", ["deadline", "cancel", "fence"])
def test_waiting_authority_cannot_send_later(reason, tmp_path):
    with server() as (url, sends):
        sdk = client(url, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")))
        active = [True]
        guard = DispatchGuard(
            time.monotonic() + (0.15 if reason == "deadline" else 5), authority=lambda: active[0]
        )

        def invoke():
            with dispatch_guard(guard), pytest.raises(DispatchDenied):
                converse(sdk)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(invoke)
            time.sleep(0.1)
            if reason == "cancel":
                guard.cancelled.set()
            elif reason == "fence":
                active[0] = False
            future.result(timeout=2)
        time.sleep(1.15)
        assert sends == []
        sdk.close()


def test_abandoned_owner_never_expires_and_stale_release_cannot_unlock(tmp_path):
    path = tmp_path / "d.sqlite3"
    original = SqliteModelDispatchStore(path)
    assert original.try_acquire("team-wide", "dead-process")
    restarted = SqliteModelDispatchStore(path)
    with pytest.raises(DispatchDenied):
        restarted.release("team-wide", "replacement")
    assert not restarted.try_acquire("team-wide", "replacement")
    with pytest.raises(DispatchDenied):
        SharedModelDispatcher(restarted).send(
            lambda: pytest.fail("unexpected send"), DispatchGuard(time.monotonic() + 0.1)
        )


def test_slow_authority_check_cannot_cross_deadline():
    def slow():
        time.sleep(0.05)
        return True

    with pytest.raises(DispatchDenied):
        DispatchGuard(time.monotonic() + 0.02, authority=slow).check()


@mock_aws
def test_dynamodb_central_conditional_owner_and_release_are_fenced():
    sdk = boto3.client("dynamodb", region_name="us-east-1")
    sdk.create_table(
        TableName="synthetic-dispatch",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    stores = [DynamoDBModelDispatchStore(sdk, table_name="synthetic-dispatch") for _ in range(6)]
    barrier = threading.Barrier(6)

    def acquire(i):
        barrier.wait(timeout=5)
        return stores[i].try_acquire("team-wide", str(i))

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(acquire, range(6)))
    assert outcomes.count(True) == 1
    winner = str(outcomes.index(True))
    fresh = DynamoDBModelDispatchStore(sdk, table_name="synthetic-dispatch")
    with pytest.raises(DispatchDenied):
        fresh.release("team-wide", "stale")
    assert not fresh.try_acquire("team-wide", "restart")
    fresh.release("team-wide", winner)
    assert fresh.try_acquire("team-wide", "restart")
    with pytest.raises(DispatchDenied):
        stores[0].release("team-wide", winner)
    sdk.close()


def test_unwrapped_real_client_and_missing_guard_are_denied(tmp_path):
    with server() as (url, sends):
        sdk = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            endpoint_url=url,
            config=Config(signature_version=UNSIGNED),
        )
        with pytest.raises(DispatchDenied):
            require_bedrock_dispatch(sdk)
        install_bedrock_dispatch(
            sdk, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3"))
        )
        with pytest.raises(DispatchDenied, match="guard_required"):
            converse(sdk)
        assert sends == []
        sdk.close()


def test_transport_disconnect_retry_has_a_new_physical_interval(tmp_path):
    with server(disconnects=1) as (url, sends):
        sdk = client(
            url, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")), attempts=2
        )
        with dispatch_guard(DispatchGuard(time.monotonic() + 10, max_sends=2)):
            converse(sdk)
        assert_spacing(sends, 2)
        sdk.close()


def test_competition_admission_checks_exact_body_url_again_on_sdk_retry(tmp_path):
    from appraisal_review.domain.competition_data import envelope_digest

    envelopes = []

    class Admission:
        def check(self, parts):
            LocalSyntheticAdmission().check(parts)
            envelopes.append(parts)
            if len(envelopes) == 2:
                raise DispatchDenied("synthetic_admission_revoked")

    with server(failures=1) as (url, sends):
        sdk = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            endpoint_url=url,
            config=Config(
                signature_version=UNSIGNED, retries={"total_max_attempts": 2, "mode": "standard"}
            ),
        )
        install_bedrock_dispatch(
            sdk,
            SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")),
            competition_admission=Admission(),
            allow_loopback_for_testing=True,
        )
        with (
            dispatch_guard(DispatchGuard(time.monotonic() + 10, max_sends=2)),
            pytest.raises(DispatchDenied, match="admission_revoked"),
        ):
            converse(sdk)
        assert len(sends) == 1
        assert len(envelopes) == 2
        assert envelope_digest(envelopes[0][:2]) == envelope_digest(envelopes[1][:2])
        assert envelopes[0][2].part_id == "bedrock.request.headers"
        assert envelopes[0][2].content != envelopes[1][2].content
        assert json.loads(envelopes[0][0].content)["messages"][0]["content"] == [{"text": "x"}]
        assert envelopes[0][1].content.endswith(b"/model/synthetic-model/converse")
        sdk.close()


def test_real_transport_missing_admission_denies_before_send(tmp_path):
    with server() as (url, sends):
        sdk = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            endpoint_url=url,
            config=Config(signature_version=UNSIGNED),
        )
        install_bedrock_dispatch(
            sdk, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3"))
        )
        with (
            dispatch_guard(DispatchGuard(time.monotonic() + 2)),
            pytest.raises(DispatchDenied, match="competition_admission_required"),
        ):
            converse(sdk)
        assert sends == []
        sdk.close()


def test_nonimmutable_body_and_admission_timeout_never_send(tmp_path):
    import io
    from types import SimpleNamespace

    from appraisal_review.adapters.aws.bedrock_dispatch import _DispatchTransport
    from appraisal_review.adapters.aws.competition_wire import WireBinding

    class NoNetwork:
        def send(self, request):
            pytest.fail("unexpected network send")

    class SlowAdmission:
        def check(self, parts):
            time.sleep(0.1)

    binding = WireBinding("bedrock-runtime")
    binding.capture(
        SimpleNamespace(name="Converse", http={"method": "POST"}),
        {"method": "POST", "url": "http://127.0.0.1/"},
    )
    transport = _DispatchTransport(
        NoNetwork(),
        SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")),
        SlowAdmission(),
        wire_binding=binding,
        endpoint_url="http://127.0.0.1/",
        allow_loopback_for_testing=True,
    )
    for body in (io.BytesIO(b"{}"), bytearray(b"{}"), "{}"):
        with (
            dispatch_guard(DispatchGuard(time.monotonic() + 2)),
            pytest.raises(DispatchDenied, match="immutable_envelope"),
        ):
            transport.send(
                SimpleNamespace(body=body, method="POST", url="http://127.0.0.1/", headers={})
            )
    with (
        dispatch_guard(DispatchGuard(time.monotonic() + 1.15)),
        pytest.raises(DispatchDenied, match="authority_expired"),
    ):
        transport.send(
            SimpleNamespace(body=b"{}", method="POST", url="http://127.0.0.1/", headers={})
        )


def test_extractor_wait_timeout_stops_sdk_worker_and_preserves_reserved_budget(tmp_path):
    import asyncio

    from appraisal_review.adapters.aws.extraction_execution import execute
    from appraisal_review.application.extraction_budget import ExtractionLedger
    from appraisal_review.domain.extraction_contracts import ExecutionBudget

    async def scenario(sdk):
        ledger = ExtractionLedger(
            ExecutionBudget(
                max_pages=1,
                max_calls=1,
                max_attempts_per_page=1,
                max_concurrency=1,
                max_input_bytes=10,
                max_context_characters=10,
                max_output_tokens=1,
                max_elapsed_seconds=0.2,
            )
        )
        ledger.begin_page("synthetic-run")
        result = await execute(
            lambda: converse(sdk),
            lambda response: pytest.fail("late response"),
            ledger=ledger,
            attempts=1,
            tokens=1,
            timeout=0.15,
            backoff_base=0,
            backoff_cap=0,
        )
        assert result.code in {"timeout", "budget_exhausted"}
        await asyncio.sleep(1.15)
        assert ledger.calls == 1
        assert ledger.reserved_output_tokens == 1
        assert ledger.in_flight == 0

    with server() as (url, sends):
        sdk = client(url, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")))
        asyncio.run(scenario(sdk))
        assert sends == []
        sdk.close()


def test_selector_cancellation_and_fence_revoke_waiting_sdk_send(tmp_path):
    import asyncio
    from pathlib import Path
    from runpy import run_path

    from appraisal_review.adapters.aws.action_selector import (
        BedrockActionSelector,
        ModelSelectorConfig,
    )
    from appraisal_review.domain.service_contracts import WorkflowState
    from appraisal_review.ports.action_selection import ActionSelectionError
    from appraisal_review.ports.model_dispatch import dispatch_authority

    helpers = run_path(str(Path(__file__).with_name("test_action_selectors.py")))

    async def scenario(sdk):
        selector = BedrockActionSelector(
            sdk, ModelSelectorConfig(model_id="synthetic-model", attempts=1)
        )
        active = [True]
        with dispatch_authority(lambda: active[0]):
            task = asyncio.create_task(
                selector.select(helpers["_input"](WorkflowState.MATERIAL_READY))
            )
            await asyncio.sleep(0.1)
            active[0] = False
            task.cancel()
            with pytest.raises(ActionSelectionError):
                await task
        await asyncio.sleep(1.15)
        assert not selector._inflight.locked()

    with server() as (url, sends):
        sdk = client(url, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")))
        asyncio.run(scenario(sdk))
        assert sends == []
        sdk.close()


@mock_aws
def test_central_dynamodb_store_serializes_actual_sdk_transports(tmp_path):
    sdk = boto3.client("dynamodb", region_name="us-east-1")
    sdk.create_table(
        TableName="synthetic-transport-dispatch",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    with server() as (url, sends):
        runtimes = [
            client(
                url,
                SharedModelDispatcher(
                    DynamoDBModelDispatchStore(sdk, table_name="synthetic-transport-dispatch")
                ),
            )
            for _ in range(2)
        ]

        def invoke(i):
            with dispatch_guard(DispatchGuard(time.monotonic() + 8)):
                return converse(runtimes[i], f"central-model-{i}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(invoke, range(2)))
        assert_spacing(sends, 2)
        for runtime in runtimes:
            runtime.close()
    sdk.close()


def test_priced_runtime_count_and_inference_share_interval_but_not_attempt_refund(tmp_path):
    from datetime import date
    from decimal import Decimal

    from appraisal_review.adapters.aws.probe_budget import PricedRuntime, ProbePricing

    pricing = ProbePricing(
        model_id="synthetic-model",
        region="us-east-1",
        rates_date=date.today(),
        rates_source="local synthetic rate fixture",
        input_per_million_usd=Decimal("1"),
        output_per_million_usd=Decimal("1"),
        maximum_input_tokens=10,
    )
    with server() as (url, sends):
        sdk = client(url, SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")))
        priced = PricedRuntime(sdk, pricing, lambda: True)
        guard = DispatchGuard(time.monotonic() + 8)
        with dispatch_guard(guard):
            priced.converse(
                modelId="synthetic-model",
                system=[{"text": "synthetic"}],
                messages=[{"role": "user", "content": [{"text": "x"}]}],
                inferenceConfig={"maxTokens": 1},
            )
        assert_spacing(sends, 2)
        assert priced.count_calls == priced.converse_calls == 1
        assert guard.sends == 1
        sdk.close()


def test_preflight_respects_inherited_deadline_before_metadata_send(tmp_path):
    from pathlib import Path
    from runpy import run_path
    from types import SimpleNamespace

    from appraisal_review.adapters.aws.extraction_preflight import preflight
    from appraisal_review.ports.document_extraction import ExtractionBoundaryError

    helpers = run_path(str(Path(__file__).with_name("test_extraction_preflight.py")))

    with server() as (url, sends):
        control = client(
            url,
            SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")),
            service="bedrock",
        )

        class Clients:
            def client(self, service, region, timeout):
                if service == "sts":
                    return SimpleNamespace(
                        meta=SimpleNamespace(region_name=region),
                        get_caller_identity=lambda: {
                            "Account": "123456789012",
                            "Arn": "arn:aws:sts::123456789012:assumed-role/ProjectReviewer/session",
                        },
                    )
                return control

        with (
            dispatch_guard(DispatchGuard(time.monotonic() + 0.15)),
            pytest.raises(ExtractionBoundaryError),
        ):
            preflight(Clients(), helpers["policy"](timeout_seconds=10))
        time.sleep(1.15)
        assert sends == []
        control.close()


def test_unknown_release_retains_owner_without_reusing_attempt_budget(tmp_path):
    store = SqliteModelDispatchStore(tmp_path / "d.sqlite3")

    class UnknownRelease:
        def try_acquire(self, scope, owner):
            return store.try_acquire(scope, owner)

        def release(self, scope, owner):
            raise OSError("synthetic ambiguous storage failure")

    guard = DispatchGuard(time.monotonic() + 3)
    calls = []
    with pytest.raises(DispatchDenied, match="release_unknown"):
        SharedModelDispatcher(UnknownRelease()).send(lambda: calls.append("sent"), guard)
    assert calls == ["sent"]
    assert guard.sends == 1
    assert not store.try_acquire("team-wide", "replacement")
    with pytest.raises(DispatchDenied):
        SharedModelDispatcher(store).send(
            lambda: pytest.fail("unexpected replay"), DispatchGuard(time.monotonic() + 0.1)
        )


def test_injected_preflight_and_priced_wrappers_cannot_hide_unthrottled_sdk(tmp_path):
    from datetime import date
    from decimal import Decimal
    from pathlib import Path
    from runpy import run_path
    from types import SimpleNamespace

    from appraisal_review.adapters.aws.extraction_preflight import preflight
    from appraisal_review.adapters.aws.probe_budget import PricedRuntime, ProbePricing
    from appraisal_review.ports.document_extraction import ExtractionBoundaryError

    helpers = run_path(str(Path(__file__).with_name("test_extraction_preflight.py")))
    with server() as (url, sends):
        sdk = boto3.client(
            "bedrock",
            region_name="us-east-1",
            endpoint_url=url,
            config=Config(signature_version=UNSIGNED),
        )

        class Clients:
            def client(self, service, region, timeout):
                if service == "sts":
                    return SimpleNamespace(
                        meta=SimpleNamespace(region_name=region),
                        get_caller_identity=lambda: {
                            "Account": "123456789012",
                            "Arn": "arn:aws:sts::123456789012:assumed-role/ProjectReviewer/session",
                        },
                    )
                return sdk

        with pytest.raises(ExtractionBoundaryError):
            preflight(Clients(), helpers["policy"]())
        with pytest.raises(DispatchDenied, match="coordinator_required"):
            PricedRuntime(
                sdk,
                ProbePricing(
                    model_id="synthetic-model",
                    region="us-east-1",
                    rates_date=date.today(),
                    rates_source="local synthetic rate fixture",
                    input_per_million_usd=Decimal("1"),
                    output_per_million_usd=Decimal("1"),
                    maximum_input_tokens=10,
                ),
                lambda: True,
            )
        assert sends == []
        sdk.close()


def test_checked_headers_and_url_are_frozen_and_unknown_metadata_is_denied(tmp_path):
    from types import SimpleNamespace

    from appraisal_review.adapters.aws.bedrock_dispatch import _DispatchTransport
    from appraisal_review.adapters.aws.competition_wire import WireBinding

    original = SimpleNamespace(
        method="POST",
        body=b"{}",
        url="https://bedrock-runtime.us-east-1.amazonaws.com/model/synthetic/converse",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "synthetic-fixture",
            "Authorization": "synthetic-excluded",
            "X-Amz-Date": "synthetic-date",
        },
    )
    observed = []

    class Admission:
        def check(self, parts):
            headers = dict(json.loads(parts[2].content))
            assert bytes.fromhex(headers["user-agent"]) == b"synthetic-fixture"
            assert "authorization" not in headers and "x-amz-date" not in headers
            original.headers["User-Agent"] = "unreviewed-modification"
            original.url = "https://unreviewed.invalid/"
            original.method = "DELETE"

    class Transport:
        def send(self, request):
            observed.append(request)
            assert request.headers["User-Agent"] == "synthetic-fixture"
            assert request.url.endswith("/model/synthetic/converse")
            assert request.method == "POST"
            with pytest.raises(TypeError):
                request.headers["User-Agent"] = "changed"
            return None

    binding = WireBinding("bedrock-runtime")
    binding.capture(
        SimpleNamespace(name="Converse", http={"method": "POST"}),
        {"method": "POST", "url": original.url},
    )
    guarded = _DispatchTransport(
        Transport(),
        SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")),
        Admission(),
        wire_binding=binding,
        endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )
    with dispatch_guard(DispatchGuard(time.monotonic() + 3)):
        guarded.send(original)
    assert len(observed) == 1

    for url, headers, reason in [
        ("https://unreviewed.invalid/", {}, "destination_invalid"),
        ("http://bedrock-runtime.us-east-1.amazonaws.com/", {}, "destination_invalid"),
        (
            "https://bedrock-runtime.us-east-1.amazonaws.com/",
            {"X-Private-Case": "secret"},
            "header_unsupported",
        ),
    ]:
        with (
            dispatch_guard(DispatchGuard(time.monotonic() + 3)),
            pytest.raises(DispatchDenied, match=reason),
        ):
            guarded.send(SimpleNamespace(body=b"{}", method="POST", url=url, headers=headers))
    assert len(observed) == 1


@pytest.mark.parametrize("service", ["bedrock-runtime", "bedrock"])
@pytest.mark.parametrize("change", ["method", "path", "query"])
def test_sdk_final_request_must_match_original_operation(service, change, tmp_path):
    with server() as (url, sends):
        sdk = client(
            url,
            SharedModelDispatcher(SqliteModelDispatchStore(tmp_path / "d.sqlite3")),
            service=service,
        )

        def mutate(request, **kwargs):
            if change == "method":
                request.method = "GET" if request.method == "POST" else "POST"
            elif change == "path":
                request.url += "/unreviewed-operation"
            else:
                request.url += "?unreviewed=true"

        sdk.meta.events.register("before-send.*.*", mutate)
        try:
            with (
                dispatch_guard(DispatchGuard(time.monotonic() + 4)),
                pytest.raises(DispatchDenied, match="wire_operation_changed"),
            ):
                if service == "bedrock-runtime":
                    converse(sdk)
                else:
                    sdk.get_foundation_model(modelIdentifier="synthetic-model")
            assert sends == []
        finally:
            sdk.close()
