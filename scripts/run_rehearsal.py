"""Plan collection or run bounded offline/localhost probes; live collectors are not installed."""

from __future__ import annotations

import http.client
import json
import sys
from typing import Any

from check_rehearsal_evidence import EvidenceError, SafeParser, _unique_pairs

from appraisal_review.domain.rehearsal import REHEARSAL_CHECKS
from appraisal_review.domain.service_contracts import ServiceErrorCode, ServiceProblem


def collection_plan() -> list[dict[str, str]]:
    classes = {
        "browser": "live_browser_automation",
        "network": "live_browser_network_capture",
        "aws": "live_aws_observation",
        "local": "local_privacy_and_pdf",
        "ci": "exact_revision_ci",
        "human": "independent_human_acceptance",
    }
    return [
        {"check": check, "execution_class": classes[spec.source], "status": "not_run"}
        for check, spec in REHEARSAL_CHECKS.items()
    ]


def localhost_probes(port: int, expectation: str) -> list[dict[str, str]]:
    """Real loopback TCP only, no proxy, redirects, authentication or cloud invocations.

    Invalid commands test transport rejection, not successful job execution. The
    operator starts the real Runtime app with all live providers disabled first.
    """
    canary = "SYNTHETIC_REHEARSAL_TRANSPORT_CANARY"
    health_status = 503 if expectation == "unready" else 200
    unavailable = ServiceProblem(code=ServiceErrorCode.CAPABILITY).model_dump(mode="json")
    invalid = ServiceProblem(code=ServiceErrorCode.VALIDATION).model_dump(mode="json")
    probes = (
        ("ping", "GET", "/ping", None, {health_status}),
        ("empty_command", "POST", "/invocations", "{}", {400, 422}),
        ("unknown_command", "POST", "/invocations", json.dumps({"unknown": canary}), {400, 422}),
        ("malformed_command", "POST", "/invocations", "{" + canary, {400, 422}),
        ("health_after_rejection", "GET", "/ping", None, {health_status}),
    )
    results = []
    for name, method, path, body, statuses in probes:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        reason = "observed"
        try:
            connection.request(method, path, body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(65_537)
            if response.status not in statuses or len(raw) > 65_536:
                reason = "unexpected_http_response"
            elif canary.encode() in raw:
                reason = "raw_input_leaked"
            else:
                value = json.loads(raw, object_pairs_hook=_unique_pairs)
                expected_health = unavailable if expectation == "unready" else {"status": "Healthy"}
                if canary in json.dumps(value, ensure_ascii=False):
                    reason = "raw_input_leaked"
                elif method == "GET" and value != expected_health:
                    reason = "unexpected_health_response"
                elif method == "POST" and value != invalid:
                    reason = "unexpected_problem_response"
        except EvidenceError:
            reason = "invalid_response_json"
        except (OSError, http.client.HTTPException, ValueError, RecursionError):
            reason = "localhost_unavailable_or_invalid"
        finally:
            connection.close()
        results.append(
            {
                "probe": name,
                "status": "passed" if reason == "observed" else "failed",
                "reason": reason,
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    report: dict[str, Any] = {
        "schema_version": "rehearsal-runner-v1",
        "publication": "Draft",
        "live_acceptance": False,
    }
    try:
        parser = SafeParser(prog="run_rehearsal", description=__doc__)
        parser.add_argument(
            "--execution", choices=("plan", "offline", "localhost", "live"), required=True
        )
        parser.add_argument("--port", type=int)
        parser.add_argument("--expect-runtime", choices=("unready", "healthy"), default="unready")
        args, remaining = parser.parse_known_args(argv)
        if args.execution == "offline":
            if args.port is not None:
                raise EvidenceError("invalid_arguments")
            from check_rehearsal_evidence import main as validate

            # Offline execution can only inspect synthetic evidence. The default
            # live acceptance gate remains a separate, explicit review command.
            if "--purpose" in remaining or any(arg.startswith("--purpose=") for arg in remaining):
                raise EvidenceError("invalid_arguments")
            return validate([*remaining, "--purpose", "synthetic-validation"])
        if remaining or (args.port is not None and args.execution != "localhost"):
            raise EvidenceError("invalid_arguments")
        report["execution"] = args.execution
        if args.execution == "plan":
            report["checks"] = collection_plan()
            report["validation_passed"] = False
            status = 0
        elif args.execution == "live":
            report["validation_passed"] = False
            report["reason"] = "live_collectors_not_integrated"
            status = 2
        else:
            if args.port is None or not 1024 <= args.port <= 65535:
                raise EvidenceError("invalid_localhost_port")
            probes = localhost_probes(args.port, args.expect_runtime)
            passed = all(probe["status"] == "passed" for probe in probes)
            report.update(
                scope="localhost_http_rejection_only",
                runtime_expectation=args.expect_runtime,
                production_ready=False,
                probes=probes,
                validation_passed=passed,
            )
            status = 0 if passed else 1
    except EvidenceError as error:
        report.update(validation_passed=False, reason=str(error))
        status = 2
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
