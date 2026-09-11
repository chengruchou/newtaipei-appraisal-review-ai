"""Validate offline acceptance receipts. No cloud calls, report files or raw diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Never, TypeVar

from pydantic import ValidationError

from appraisal_review.domain.rehearsal import (
    MAX_REHEARSAL_BYTES,
    REHEARSAL_CHECKS,
    REHEARSAL_SCENARIOS,
    RehearsalManifest,
    RehearsalModel,
    RehearsalReceipt,
    RehearsalScenario,
    RehearsalTarget,
    RehearsalTrust,
    rehearsal_canonical,
    rehearsal_context,
    rehearsal_digest,
)

T = TypeVar("T", bound=RehearsalModel)


class EvidenceError(Exception):
    """Only static reason codes may cross the report boundary."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise EvidenceError("invalid_arguments")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def _parse(data: bytes, model: type[T]) -> T:
    try:
        json.loads(data, object_pairs_hook=_unique_pairs)
        return model.model_validate_json(data)
    except (ValidationError, ValueError, UnicodeError, RecursionError):
        # ValidationError locations and input values can contain raw canaries.
        raise EvidenceError("invalid_record") from None


def _read(path: Path) -> bytes:
    try:
        # Receipts are regular, bounded JSON. Never follow a link to private input.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REHEARSAL_BYTES:
                raise EvidenceError("invalid_file")
            data = stream.read(MAX_REHEARSAL_BYTES + 1)
            if len(data) > MAX_REHEARSAL_BYTES:
                raise EvidenceError("invalid_file")
            return data
    except (OSError, ValueError):
        raise EvidenceError("unreadable_file") from None


def _signature(receipt: RehearsalReceipt, trust: RehearsalTrust) -> None:
    observation = receipt.observation
    key = next((key for key in trust.collectors if key.key_id == receipt.key_id), None)
    if key is None or key.revoked:
        raise EvidenceError("untrusted_collector")
    if key.source != observation.source or key.mode != observation.mode:
        raise EvidenceError("wrong_trust_source")
    if not key.valid_from <= observation.observed_at <= key.valid_until:
        raise EvidenceError("collector_expired")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise EvidenceError("signature_verifier_unavailable") from None
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key.public_key)).verify(
            bytes.fromhex(receipt.signature),
            b"rehearsal-receipt-v1\n"
            + receipt.key_id.encode("ascii")
            + b"\n"
            + rehearsal_canonical(observation),
        )
    except (InvalidSignature, ValueError):
        raise EvidenceError("invalid_signature") from None


def _observation(
    receipt: RehearsalReceipt,
    manifest: RehearsalManifest,
    scenario: RehearsalScenario,
    check: str,
    trust: RehearsalTrust,
) -> None:
    _signature(receipt, trust)
    value = receipt.observation
    spec = REHEARSAL_CHECKS[check]
    if value.check != check or value.source != spec.source:
        raise EvidenceError("wrong_observation_source")
    if value.campaign_id != manifest.campaign_id or value.mode != manifest.mode:
        raise EvidenceError("campaign_mismatch")
    if value.target_digest != rehearsal_digest(manifest.target):
        raise EvidenceError("target_mismatch")
    if value.context_digest != rehearsal_context(scenario):
        raise EvidenceError("context_mismatch")
    if scenario.binding is None or value.correlation != scenario.binding.correlation:
        raise EvidenceError("correlation_mismatch")
    if not manifest.started_at <= value.observed_at <= manifest.finished_at:
        raise EvidenceError("observation_outside_campaign")
    if value.status != "passed" or value.observed != spec.expected or value.violations != 0:
        raise EvidenceError("observation_not_passed")
    if value.output is not None and value.output != scenario.binding.output:
        raise EvidenceError("artifact_mismatch")
    if spec.output_required and value.output is None:
        raise EvidenceError("artifact_missing")


def check_evidence(
    manifest: RehearsalManifest,
    trust: RehearsalTrust,
    target: RehearsalTarget,
    campaign: str,
    evidence_directory: Path,
    purpose: str,
    now: int,
) -> tuple[dict[str, object], int]:
    """Inspect all fixed gates. Report contains constants/counts, never input strings."""
    issues: list[dict[str, str]] = []

    def block(gate: str, reason: str) -> None:
        issues.append({"gate": gate, "reason": reason})

    if manifest.target != target:
        block("target", "stale_or_different_target")
    if manifest.campaign_id != campaign:
        block("campaign", "campaign_mismatch")
    if manifest.finished_at > now + 300 or now - manifest.finished_at > 86_400:
        block("campaign", "stale_or_future_campaign")
    mode = "synthetic" if purpose == "synthetic-validation" else "live_sandbox"
    if manifest.mode != mode:
        block("mode", "wrong_evidence_mode")
    scenarios = {scenario.name: scenario for scenario in manifest.scenarios}
    checked = 0
    for name in REHEARSAL_SCENARIOS:
        scenario = scenarios.get(name)
        if scenario is None:
            block(name, "missing_scenario")
            continue
        if scenario.status != "passed":
            block(name, "scenario_not_passed")
        references = {reference.check: reference for reference in scenario.evidence}
        for check, spec in REHEARSAL_CHECKS.items():
            if spec.scenario != name:
                continue
            reference = references.get(check)
            if reference is None:
                block(check, "missing_evidence")
                continue
            try:
                raw = _read(evidence_directory / f"{reference.digest}.json")
                if len(raw) != reference.size_bytes:
                    raise EvidenceError("receipt_size_mismatch")
                if hashlib.sha256(raw).hexdigest() != reference.digest:
                    raise EvidenceError("receipt_hash_mismatch")
                receipt = _parse(raw, RehearsalReceipt)
                _observation(receipt, manifest, scenario, check, trust)
                checked += 1
            except EvidenceError as error:
                block(check, str(error))
    valid = not issues
    live = valid and purpose == "live-acceptance"
    report: dict[str, object] = {
        "schema_version": "rehearsal-report-v1",
        "validation_passed": valid,
        "live_acceptance": live,
        "publication": "Ready" if live else "Draft",
        "scope": "live_sandbox_synthetic_cases" if live else "offline_evidence_validation",
        "required_checks": len(REHEARSAL_CHECKS),
        "verified_receipts": checked,
        "blockers": issues,
    }
    return report, 0 if valid else 1


def main(argv: list[str] | None = None) -> int:
    try:
        parser = SafeParser(prog="check_rehearsal_evidence", description=__doc__)
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--trust", type=Path, required=True)
        parser.add_argument("--target", type=Path, required=True)
        parser.add_argument("--campaign", required=True)
        parser.add_argument("--evidence-directory", type=Path, required=True)
        parser.add_argument(
            "--purpose",
            choices=("live-acceptance", "synthetic-validation"),
            default="live-acceptance",
        )
        args = parser.parse_args(argv)
        manifest = _parse(_read(args.manifest), RehearsalManifest)
        trust = _parse(_read(args.trust), RehearsalTrust)
        target = _parse(_read(args.target), RehearsalTarget)
        report, status = check_evidence(
            manifest,
            trust,
            target,
            args.campaign,
            args.evidence_directory,
            args.purpose,
            int(time.time()),
        )
    except EvidenceError as error:
        report = {
            "schema_version": "rehearsal-report-v1",
            "validation_passed": False,
            "live_acceptance": False,
            "publication": "Draft",
            "scope": "offline_evidence_validation",
            "blockers": [{"gate": "input", "reason": str(error)}],
        }
        status = 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    sys.exit(main())
