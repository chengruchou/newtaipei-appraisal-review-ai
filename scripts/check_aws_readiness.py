#!/usr/bin/env python3
"""Report live AWS readiness as Ready/Missing/Blocked. Read-only; approves nothing.

This probe answers one question for a deployment operator: which competition
deployment inputs already exist in the current account, which are absent, and which
are refused. It creates, deletes and modifies nothing, invokes no model, and sends
no case material. Every Bedrock call is serialized through the shared dispatcher.

Deliberate boundaries:

- It is not the approved competition entrypoint. `adapters/aws/competition_runtime.py`
  remains that entrypoint, and this probe installs no data-admission gate because it
  transmits no document, prompt or case content that would need one.
- A `ready` state means the resource or route was observed, never that the organizer
  permits it, that the role's effective permissions were proven, or that data may be
  sent. `live_acceptance` stays `not_performed`.
- Identity and resource names are private operator evidence. They are reported as
  digests unless `--emit-identity` is given, and the output must not be committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from appraisal_review.adapters.aws.document_storage import (
    S3DocumentConfiguration,
    S3DocumentStorage,
)
from appraisal_review.adapters.aws.routing_discovery import discover_routing
from appraisal_review.adapters.local.sqlite_model_dispatch import SqliteModelDispatchStore
from appraisal_review.application.competition_preflight import check_model_destinations
from appraisal_review.application.model_dispatch import SharedModelDispatcher
from appraisal_review.domain.competition_profile import PRIMARY_REGIONS, CompetitionProfile
from appraisal_review.domain.document_transfer import DocumentFault
from appraisal_review.ports.document_extraction import ExtractionBoundaryError
from appraisal_review.ports.model_dispatch import (
    DispatchDenied,
    DispatchGuard,
    current_dispatch_guard,
)

State = Literal["ready", "missing", "blocked"]
MODEL_KINDS = ("foundation", "system_profile", "application_profile")
_BEDROCK = {"bedrock", "bedrock-runtime"}


@dataclass(frozen=True)
class Check:
    name: str
    state: State
    detail: str
    source: str


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def error_code(error: Exception) -> str:
    """SDK messages can carry resource names and endpoints; keep only the code."""
    response = getattr(error, "response", {})
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str) and code:
            return code
    return type(error).__name__


class ThrottledClients:
    """Lazy read-only clients. Bedrock calls wait the shared dispatcher interval."""

    def __init__(self, dispatcher: SharedModelDispatcher, *, timeout: float) -> None:
        self.dispatcher, self.timeout = dispatcher, timeout
        self._session: Any = None

    def client(self, service: str, region: str, timeout: float) -> Any:
        import boto3
        from botocore.config import Config

        if self._session is None:
            self._session = boto3.Session()
        client = self._session.client(
            service,
            region_name=region,
            config=Config(
                connect_timeout=min(10, timeout / 2),
                read_timeout=timeout / 2,
                retries={"total_max_attempts": 1},
            ),
        )
        return _Throttled(client, self.dispatcher, timeout) if service in _BEDROCK else client


class _Throttled:
    """Serialize each Bedrock operation, including the control plane, through dispatch."""

    def __init__(self, client: Any, dispatcher: SharedModelDispatcher, timeout: float) -> None:
        self._client, self._dispatcher, self._timeout = client, dispatcher, timeout

    @property
    def meta(self) -> Any:
        return self._client.meta

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if not callable(target):
            return target

        def operation(**kwargs: Any) -> Any:
            try:
                guard = current_dispatch_guard()
            except DispatchDenied:
                guard = DispatchGuard(time.monotonic() + self._timeout)
            return self._dispatcher.send(lambda: target(**kwargs), guard)

        return operation


def probe(name: str, source: str, call: Callable[[], tuple[State, str]]) -> Check:
    try:
        state, detail = call()
    except (DocumentFault, ExtractionBoundaryError) as fault:
        return Check(name, "blocked", type(fault).__name__ + ":" + str(fault), source)
    except DispatchDenied as denied:
        return Check(name, "blocked", "dispatch:" + str(denied), source)
    except Exception as error:
        # Report the SDK error code only. Messages carry resource names and endpoints.
        return Check(name, "blocked", error_code(error), source)
    return Check(name, state, detail, source)


def parse_model(value: str) -> tuple[str, str]:
    identifier, _, kind = value.rpartition(":")
    if not identifier or kind not in MODEL_KINDS:
        raise argparse.ArgumentTypeError("Use MODEL_ID:" + "|".join(MODEL_KINDS))
    return identifier, kind


def count_only(name: str, source: str, call: Callable[[], list[str]], emit: bool) -> Check:
    def observe() -> tuple[State, str]:
        names = call()
        detail = ", ".join(sorted(names)) if emit and names else f"{len(names)} existing"
        return ("ready" if names else "missing"), detail

    return probe(name, source, observe)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True, help="Single primary deployment region")
    parser.add_argument(
        "--model",
        action="append",
        type=parse_model,
        default=[],
        metavar="MODEL_ID:KIND",
        help="Requested model identifier and kind; repeatable",
    )
    parser.add_argument(
        "--permit-region",
        action="append",
        default=[],
        help="Additional region the organizer permits for routing; repeatable",
    )
    parser.add_argument("--bucket", help="Existing sanitized document bucket to inspect")
    parser.add_argument("--dispatch-table", help="Existing shared dispatch DynamoDB table")
    parser.add_argument("--expect-account", help="Account the operator intends to use")
    parser.add_argument("--expect-role-arn", help="Role the operator intends to assume")
    parser.add_argument("--profile", type=Path, help="Competition profile to compare routing with")
    parser.add_argument(
        "--dispatch-state",
        type=Path,
        default=Path("artifacts/aws-readiness-dispatch.sqlite3"),
        help="Host-local dispatch interval store; proves host coordination only",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--emit-identity",
        action="store_true",
        help="Print account, role and resource names for a private operator record",
    )
    args = parser.parse_args(argv)

    checks: list[Check] = []
    routing: list[dict[str, Any]] = []
    identity: dict[str, Any] = {}
    args.dispatch_state.parent.mkdir(parents=True, exist_ok=True)
    dispatcher = SharedModelDispatcher(SqliteModelDispatchStore(args.dispatch_state))
    clients = ThrottledClients(dispatcher, timeout=args.timeout)
    permitted = frozenset({args.region, *args.permit_region})

    checks.append(
        Check(
            "primary_region_permitted",
            "ready" if args.region in PRIMARY_REGIONS else "blocked",
            args.region,
            "PDF p1 general rule6",
        )
    )

    account: str | None = None
    role_arn: str | None = None

    def caller() -> tuple[State, str]:
        nonlocal account, role_arn
        response = clients.client("sts", args.region, args.timeout).get_caller_identity()
        account, role_arn = response["Account"], response["Arn"]
        identity["account_id_sha256"] = digest(account)
        identity["caller_arn_sha256"] = digest(role_arn)
        if args.emit_identity:
            identity["account_id"], identity["caller_arn"] = account, role_arn
        if args.expect_account is not None and args.expect_account != account:
            return "blocked", "resolved account differs from the intended account"
        if args.expect_role_arn is not None:
            # An assumed-role session ARN is sts::account:assumed-role/Name/Session,
            # so compare the role name rather than the whole IAM role ARN.
            name = args.expect_role_arn.rpartition("/")[2]
            if f":assumed-role/{name}/" not in role_arn:
                return "blocked", "resolved session does not belong to the intended role"
        return "ready", "assumed session resolved"

    checks.append(probe("caller_identity", "operator approval", caller))

    if account is None:
        checks.append(
            Check("role_effective_permissions", "blocked", "identity unresolved", "operator")
        )
    else:
        for identifier, kind in args.model:
            if kind != "foundation":

                def advertised(identifier: str = identifier) -> tuple[State, str]:
                    # Advertised routing answers the regional-compliance question even when a
                    # destination cannot be verified. It is not a pinnable destination set.
                    observation = clients.client(
                        "bedrock", args.region, args.timeout
                    ).get_inference_profile(inferenceProfileIdentifier=identifier)
                    regions = sorted(
                        {
                            str(route["modelArn"]).split(":")[3]
                            for route in observation.get("models", [])
                        }
                    )
                    outside = [region for region in regions if region not in permitted]
                    detail = "advertised regions: " + ", ".join(regions)
                    if outside:
                        return "blocked", detail + "; unpermitted: " + ", ".join(outside)
                    return "ready", detail

                checks.append(
                    probe(
                        f"model_advertised_routing[{identifier}]",
                        "PDF p1 general rule6",
                        advertised,
                    )
                )

            def discover(identifier: str = identifier, kind: str = kind) -> tuple[State, str]:
                assert account is not None
                snapshot = discover_routing(
                    clients,
                    model_id=identifier,
                    kind=kind,  # type: ignore[arg-type]
                    region=args.region,
                    account_id=account,
                    timeout_seconds=args.timeout,
                )
                outside = snapshot.routes_outside(permitted)
                routing.append(
                    {
                        "model_id": snapshot.model_id,
                        "kind": snapshot.kind,
                        "destinations": [d.arn for d in snapshot.destinations],
                        "destination_regions": list(snapshot.destination_regions),
                        "destination_snapshot_sha256": snapshot.digest,
                        "regions_outside_permitted": list(outside),
                        "observed_at": snapshot.observed_at.isoformat(),
                    }
                )
                if outside:
                    return "blocked", "routes to unpermitted regions: " + ", ".join(outside)
                return "ready", f"{len(snapshot.destinations)} pinned destinations"

            checks.append(probe(f"model_routing[{identifier}]", "PDF p1 general rule6", discover))

            def availability(identifier: str = identifier) -> tuple[State, str]:
                control = clients.client("bedrock", args.region, args.timeout)
                name = identifier.split(".", 1)[1] if identifier.startswith("us.") else identifier
                observed = control.get_foundation_model_availability(modelId=name)
                fields = {
                    "agreement": observed.get("agreementAvailability", {}).get("status"),
                    "authorization": observed.get("authorizationStatus"),
                    "entitlement": observed.get("entitlementAvailability"),
                    "region": observed.get("regionAvailability"),
                }
                detail = ", ".join(f"{k}={v}" for k, v in fields.items())
                state: State = "ready" if fields["entitlement"] == "AVAILABLE" else "missing"
                return state, detail

            checks.append(
                probe(f"model_availability[{identifier}]", "PDF p1 Bedrock rules2-3", availability)
            )

        checks.append(
            Check(
                "role_effective_permissions",
                "missing",
                "effective permission evidence is an operator deliverable; this probe "
                "observes only the operations it performed",
                "PDF p1 general rule7",
            )
        )

    def bucket_posture() -> tuple[State, str]:
        if not args.bucket or account is None:
            return "missing", "no existing sanitized bucket supplied"
        storage = S3DocumentStorage(
            clients.client("s3", args.region, args.timeout),
            S3DocumentConfiguration(args.bucket, account, uuid4()),
        )
        storage.check_configuration()
        return "ready", "versioning, public-access block, encryption and ownership verified"

    checks.append(probe("sanitized_bucket_posture", "PDF p1 general rule1", bucket_posture))

    def dispatch_table() -> tuple[State, str]:
        if not args.dispatch_table or account is None:
            return "missing", "no central dispatch table supplied"
        table = clients.client("dynamodb", args.region, args.timeout).describe_table(
            TableName=args.dispatch_table
        )["Table"]
        if table["TableStatus"] != "ACTIVE":
            return "blocked", "table is not ACTIVE"
        return "ready", table["TableArn"] if args.emit_identity else "ACTIVE"

    checks.append(probe("shared_dispatch_store", "PDF p1 Bedrock rule1", dispatch_table))

    inventory: Iterable[tuple[str, Callable[[], list[str]]]] = (
        (
            "existing_buckets",
            lambda: [
                b["Name"]
                for b in clients.client("s3", args.region, args.timeout).list_buckets()["Buckets"]
            ],
        ),
        (
            "existing_tables",
            lambda: list(
                clients.client("dynamodb", args.region, args.timeout).list_tables()["TableNames"]
            ),
        ),
        (
            "existing_repositories",
            lambda: [
                r["repositoryName"]
                for r in clients.client("ecr", args.region, args.timeout).describe_repositories()[
                    "repositories"
                ]
            ],
        ),
        (
            "existing_runtimes",
            lambda: [
                r["agentRuntimeName"]
                for r in clients.client(
                    "bedrock-agentcore-control", args.region, args.timeout
                ).list_agent_runtimes(maxResults=20)["agentRuntimes"]
            ],
        ),
    )
    for name, call in inventory:
        checks.append(count_only(name, "PDF p1 general rule5", call, args.emit_identity))

    if args.profile is not None:

        def compare() -> tuple[State, str]:
            assert args.profile is not None
            approved = CompetitionProfile.model_validate_json(args.profile.read_bytes())
            mismatched = [
                observation["model_id"]
                for observation in routing
                if not check_model_destinations(
                    approved, str(observation["model_id"]), observation["destinations"]
                )
                or not any(
                    use.model_id == observation["model_id"]
                    and use.destination_snapshot_sha256
                    == observation["destination_snapshot_sha256"]
                    for use in approved.models
                )
            ]
            if not routing:
                return "missing", "no routing was discovered to compare"
            if mismatched:
                return "blocked", "profile routing differs for: " + ", ".join(sorted(mismatched))
            return "ready", "profile records the complete discovered routing"

        checks.append(probe("profile_routing_matches", "PDF p1 Bedrock rules2-3", compare))

    blocked = [c for c in checks if c.state == "blocked"]
    report = {
        "schema_version": "aws-readiness-v1",
        "status": "blocked" if blocked else "observed",
        "live_acceptance": "not_performed",
        "data_admission": "not_granted",
        "observed_at": datetime.now(UTC).isoformat(),
        "region": args.region,
        "permitted_routing_regions": sorted(permitted),
        "dispatch": {
            "scope": dispatcher.scope,
            "interval_seconds": dispatcher.interval_seconds,
            "coordination": "host_local_sqlite",
        },
        "identity": identity,
        "routing": routing,
        "checks": [asdict(c) for c in checks],
        "ready": sorted(c.name for c in checks if c.state == "ready"),
        "missing": sorted(c.name for c in checks if c.state == "missing"),
        "blocked": sorted(c.name for c in blocked),
    }
    print(json.dumps(report, indent=2))
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
