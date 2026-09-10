"""Synthetic signed fixtures exercise the offline gate, never live acceptance.

Every case, alias, digest and signing key in this file is invented test material.
No browser, AWS, reviewer approval or production provider runs in these tests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import jsonschema
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from appraisal_review.domain.rehearsal import (
    REHEARSAL_CHECKS,
    REHEARSAL_SCENARIOS,
    RehearsalManifest,
    RehearsalObservation,
    RehearsalScenario,
    RehearsalTarget,
    rehearsal_canonical,
    rehearsal_context,
    rehearsal_digest,
    rehearsal_schema,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_rehearsal_evidence.py"
CANARY = "SYNTHETIC_PRIVATE_CANARY_987654321012"


def digest(label: str) -> str:
    return hashlib.sha256(f"synthetic-only:{label}".encode()).hexdigest()


def alias(label: str) -> str:
    return "r-" + digest(label)[:32]


def parsed(model, data):
    return model.model_validate_json(json.dumps(data))


def binding(label, *, output=True):
    correlation = {
        field: alias(f"{label}-{field}")
        for field in ("case_id", "job_id", "run_id", "attempt_id", "trace_id", "revision_id")
    }
    value = {
        field: digest(f"{label}-{field}")
        for field in (
            "source_digest",
            "source_version_digest",
            "source_snapshot_digest",
            "privacy_attestation_digest",
            "input_digest",
            "decision_digest",
            "revision_digest",
            "material_digest",
            "confirmation_digest",
            "approval_digest",
            "rules_digest",
            "template_digest",
            "field_map_digest",
            "golden_digest",
        )
    }
    value["correlation"] = correlation
    value["output"] = None
    if output:
        value["output"] = {
            "artifact_id": alias(f"{label}-artifact"),
            "content_digest": digest(f"{label}-output"),
            "size_bytes": 256,
            "object_version_digest": digest(f"{label}-object-version"),
            "manifest_digest": digest(f"{label}-manifest"),
            "manifest_version": 1,
            "run_id": correlation["run_id"],
            "attempt_id": correlation["attempt_id"],
            "revision_digest": value["revision_digest"],
            "template_digest": value["template_digest"],
            "field_map_digest": value["field_map_digest"],
        }
    return value


class SyntheticBundle:
    """Creates only synthetic-mode receipts, with keys unusable as a live trust root."""

    def __init__(self, path):
        self.path = path
        self.receipts = path / "receipts"
        self.receipts.mkdir()
        self.now = int(time.time())
        self.keys = {}
        self.trust = {"schema_version": "rehearsal-trust-v1", "collectors": []}
        for source in ("browser", "network", "aws", "local", "ci", "human"):
            key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(digest(f"test-key-{source}")))
            self.keys[source] = key
            self.trust["collectors"].append(
                {
                    "key_id": alias(f"test-key-{source}"),
                    "public_key": key.public_key()
                    .public_bytes(Encoding.Raw, PublicFormat.Raw)
                    .hex(),
                    "source": source,
                    "mode": "synthetic",
                    "revoked": False,
                    "valid_from": self.now - 3600,
                    "valid_until": self.now + 3600,
                }
            )
        self.target = {
            field: digest(field) for field in RehearsalTarget.model_fields if field != "code_commit"
        }
        self.target["code_commit"] = "a" * 40
        self.target.update(
            pr_head_commit="a" * 40,
            pr_base_commit="b" * 40,
            ci_tested_commit="a" * 40,
            pull_request_number=31,
            ci_namespace="issue31-exact-revision-v1",
            ci_run_attempt=1,
        )
        self.manifest = {
            "schema_version": "rehearsal-v1",
            "data_classification": "synthetic_cases_only",
            "mode": "synthetic",
            "campaign_id": alias("campaign"),
            "started_at": self.now - 60,
            "finished_at": self.now,
            "target": copy.deepcopy(self.target),
            "scenarios": [],
        }
        for name in REHEARSAL_SCENARIOS:
            current = binding(
                name,
                output=name
                in {
                    "happy",
                    "human",
                    "worker_recovery",
                    "outbox_recovery",
                    "output",
                },
            )
            scenario = {
                "name": name,
                "status": "passed",
                "binding": current,
                "previous": None,
                "task_id": None,
                "rollback_image_digest": None,
                "evidence": [],
            }
            if name == "human":
                old = binding("previous-human", output=False)
                for field in ("case_id", "job_id"):
                    old["correlation"][field] = current["correlation"][field]
                scenario["previous"] = old
                scenario["task_id"] = alias("human-task")
            if name == "worker_recovery":
                old = copy.deepcopy(current)
                old["output"] = None
                old["correlation"]["attempt_id"] = alias("expired-attempt")
                scenario["previous"] = old
            if name == "rollback":
                scenario["rollback_image_digest"] = digest("previous-image")
            self.manifest["scenarios"].append(scenario)
            for check, spec in REHEARSAL_CHECKS.items():
                if spec.scenario == name:
                    observation = {
                        "schema_version": "rehearsal-observation-v1",
                        "campaign_id": self.manifest["campaign_id"],
                        "mode": "synthetic",
                        "check": check,
                        "source": spec.source,
                        "target_digest": rehearsal_digest(parsed(RehearsalTarget, self.target)),
                        "context_digest": rehearsal_context(parsed(RehearsalScenario, scenario)),
                        "correlation": copy.deepcopy(current["correlation"]),
                        "observed_at": self.now - 1,
                        "status": "passed",
                        "observed": spec.expected,
                        "sample_count": 1,
                        "violations": 0,
                        "capture_digest": digest(f"{check}-capture"),
                        "capture_size_bytes": 128,
                        "capture_version_digest": digest(f"{check}-capture-version"),
                        "output": copy.deepcopy(current["output"])
                        if spec.output_required
                        else None,
                    }
                    self.save_receipt(check, observation)

    def scenario(self, name):
        return next(s for s in self.manifest["scenarios"] if s["name"] == name)

    def ref(self, check):
        return next(
            r for r in self.scenario(check.split(".")[0])["evidence"] if r["check"] == check
        )

    def observation(self, check):
        ref = self.ref(check)
        return json.loads((self.receipts / f"{ref['digest']}.json").read_text())["observation"]

    def save_receipt(self, check, observation, signer=None):
        source = signer or observation["source"]
        key_id = alias(f"test-key-{source}")
        value = parsed(RehearsalObservation, observation)
        signature = (
            self.keys[source]
            .sign(b"rehearsal-receipt-v1\n" + key_id.encode() + b"\n" + rehearsal_canonical(value))
            .hex()
        )
        raw = json.dumps(
            {"key_id": key_id, "observation": observation, "signature": signature}
        ).encode()
        sha = hashlib.sha256(raw).hexdigest()
        (self.receipts / f"{sha}.json").write_bytes(raw)
        refs = self.scenario(check.split(".")[0])["evidence"]
        refs[:] = [ref for ref in refs if ref["check"] != check]
        refs.append({"check": check, "digest": sha, "size_bytes": len(raw)})

    def write(self):
        for name, value in (
            ("manifest", self.manifest),
            ("trust", self.trust),
            ("target", self.target),
        ):
            (self.path / f"{name}.json").write_text(json.dumps(value))

    def command(self, purpose="synthetic-validation"):
        return [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(self.path / "manifest.json"),
            "--trust",
            str(self.path / "trust.json"),
            "--target",
            str(self.path / "target.json"),
            "--campaign",
            self.manifest["campaign_id"],
            "--evidence-directory",
            str(self.receipts),
            "--purpose",
            purpose,
        ]

    def run(self, *, purpose="synthetic-validation", write=True, command=None):
        if write:
            self.write()
        result = subprocess.run(
            command or self.command(purpose),
            capture_output=True,
            text=True,
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        assert not result.stderr
        assert CANARY not in result.stdout
        assert str(self.path) not in result.stdout
        report = json.loads(result.stdout)
        # Synthetic fixtures can never attest a live result, even on a local positive path.
        assert report["live_acceptance"] is False
        assert report["publication"] == "Draft"
        return result.returncode, report


@pytest.fixture
def bundle(tmp_path):
    return SyntheticBundle(tmp_path)


def reasons(report):
    return {item["reason"] for item in report["blockers"]}


def test_complete_synthetic_cli_is_valid_but_remains_draft(bundle):
    code, report = bundle.run()
    assert code == 0
    assert report["validation_passed"] is True
    assert report["verified_receipts"] == len(REHEARSAL_CHECKS)
    assert report["blockers"] == []


def test_default_live_gate_rejects_complete_synthetic_evidence(bundle):
    bundle.write()
    command = bundle.command()[:-2]
    code, report = bundle.run(write=False, command=command)
    assert code == 1
    assert "wrong_evidence_mode" in reasons(report)


@pytest.mark.parametrize("scenario", REHEARSAL_SCENARIOS)
def test_every_missing_scenario_blocks_cli(bundle, scenario):
    bundle.manifest["scenarios"] = [
        s for s in bundle.manifest["scenarios"] if s["name"] != scenario
    ]
    code, report = bundle.run()
    assert code == 1
    assert {"gate": scenario, "reason": "missing_scenario"} in report["blockers"]


@pytest.mark.parametrize("status", ["skipped", "not_run", "unknown", "failed"])
def test_nonpassing_scenario_and_observation_never_count_as_pass(bundle, status):
    bundle.scenario("human")["status"] = status
    observation = bundle.observation("happy.authenticated_login")
    observation["status"] = status
    bundle.save_receipt("happy.authenticated_login", observation)
    code, report = bundle.run()
    assert code == 1
    assert {"scenario_not_passed", "observation_not_passed"} <= reasons(report)


@pytest.mark.parametrize(
    "field",
    [
        key
        for key in RehearsalTarget.model_fields
        if key.endswith("_digest") or key == "pr_head_commit"
    ],
)
def test_exact_release_pins_reject_stale_head_image_and_configuration(bundle, field):
    bundle.target[field] = "b" * (40 if field.endswith("_commit") else 64)
    code, report = bundle.run()
    assert code == 1
    assert "stale_or_different_target" in reasons(report)


def test_no_fallback_for_missing_integration_and_ci(bundle):
    for name in ("integrations", "ci"):
        scenario = bundle.scenario(name)
        scenario.update(status="not_run", evidence=[])
    code, report = bundle.run()
    assert code == 1
    for check, spec in REHEARSAL_CHECKS.items():
        if spec.scenario in {"integrations", "ci"}:
            assert {"gate": check, "reason": "missing_evidence"} in report["blockers"]


def test_stale_deployed_head_and_ci_commit_are_rejected(bundle):
    bundle.target["code_commit"] = "c" * 40
    bundle.target["ci_tested_commit"] = "c" * 40
    code, report = bundle.run()
    assert code == 1
    assert "stale_or_different_target" in reasons(report)
    bundle.target["ci_tested_commit"] = "d" * 40
    code, report = bundle.run()
    assert code == 2
    assert "invalid_record" in reasons(report)


def test_offline_runner_validates_synthetic_bundle_without_live_promotion(bundle):
    command = bundle.command()
    command[1] = str(ROOT / "scripts/run_rehearsal.py")
    command = [*command[:-2], "--execution", "offline"]
    code, report = bundle.run(command=command)
    assert code == 0
    assert report["validation_passed"] is True


@pytest.mark.parametrize("source", ["browser", "network", "aws", "local", "ci", "human"])
def test_untrusted_signer_cannot_cross_a_source_boundary(bundle, source):
    check = next(key for key, spec in REHEARSAL_CHECKS.items() if spec.source == source)
    other = "local" if source != "local" else "ci"
    bundle.save_receipt(check, bundle.observation(check), signer=other)
    code, report = bundle.run()
    assert code == 1
    assert "wrong_trust_source" in reasons(report)


def test_local_result_cannot_be_relabelled_as_browser_evidence(bundle):
    check = "happy.authenticated_login"
    observation = bundle.observation(check)
    observation["source"] = "local"
    bundle.save_receipt(check, observation)
    code, report = bundle.run()
    assert code == 1
    assert "wrong_observation_source" in reasons(report)


def test_mode_relabelling_does_not_upgrade_synthetic_signing_authority(bundle):
    bundle.manifest["mode"] = "live_sandbox"
    check = "happy.authenticated_login"
    observation = bundle.observation(check)
    observation["mode"] = "live_sandbox"
    bundle.save_receipt(check, observation)
    code, report = bundle.run(purpose="live-acceptance")
    assert code == 1
    assert "wrong_trust_source" in reasons(report)


@pytest.mark.parametrize(
    "field", ["case_id", "job_id", "run_id", "attempt_id", "trace_id", "revision_id"]
)
def test_signed_correlation_mismatch_is_rejected(bundle, field):
    check = "happy.authenticated_login"
    observation = bundle.observation(check)
    observation["correlation"][field] = alias("another-correlation")
    bundle.save_receipt(check, observation)
    code, report = bundle.run()
    assert code == 1
    assert "correlation_mismatch" in reasons(report)


@pytest.mark.parametrize("field", ["context_digest", "target_digest", "campaign_id"])
def test_receipts_cannot_be_spliced_between_contexts(bundle, field):
    check = "happy.authenticated_login"
    observation = bundle.observation(check)
    observation[field] = alias("other") if field == "campaign_id" else digest("other")
    bundle.save_receipt(check, observation)
    code, report = bundle.run()
    assert code == 1
    assert reasons(report) & {"context_mismatch", "target_mismatch", "campaign_mismatch"}


@pytest.mark.parametrize(
    "field",
    [
        "content_digest",
        "size_bytes",
        "object_version_digest",
        "manifest_digest",
        "manifest_version",
        "run_id",
        "attempt_id",
        "revision_digest",
        "template_digest",
        "field_map_digest",
    ],
)
def test_actual_download_and_manifest_artifact_links_must_match(bundle, field):
    check = "output.downloaded_hash_size_version"
    observation = bundle.observation(check)
    original = observation["output"][field]
    observation["output"][field] = (
        original + 1
        if isinstance(original, int)
        else alias("wrong")
        if field.endswith("_id")
        else digest("wrong")
    )
    bundle.save_receipt(check, observation)
    code, report = bundle.run()
    assert code == 1
    assert "artifact_mismatch" in reasons(report)


def test_artifact_attestation_cannot_be_omitted(bundle):
    check = "happy.pdf_written"
    observation = bundle.observation(check)
    observation["output"] = None
    bundle.save_receipt(check, observation)
    code, report = bundle.run()
    assert code == 1
    assert "artifact_missing" in reasons(report)


@pytest.mark.parametrize(
    "field",
    [
        "source_digest",
        "source_version_digest",
        "source_snapshot_digest",
        "input_digest",
        "decision_digest",
        "revision_digest",
        "template_digest",
        "golden_digest",
        "confirmation_digest",
        "approval_digest",
    ],
)
def test_signed_context_binds_all_material_versions(bundle, field):
    scenario = bundle.scenario("authorization")
    scenario["binding"][field] = digest("replaced-material")
    code, report = bundle.run()
    assert code == 1
    assert "context_mismatch" in reasons(report)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("bytes", "receipt_hash_mismatch"),
        ("size", "receipt_size_mismatch"),
        ("missing", "unreadable_file"),
        ("signature", "invalid_signature"),
    ],
)
def test_hash_size_existence_and_signature_are_checked(bundle, mutation, reason):
    ref = bundle.ref("happy.authenticated_login")
    path = bundle.receipts / f"{ref['digest']}.json"
    if mutation == "size":
        ref["size_bytes"] += 1
    elif mutation == "missing":
        path.unlink()
    elif mutation == "bytes":
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'"observed": 1', b'"observed": 2'))
    else:
        value = json.loads(path.read_text())
        value["signature"] = "0" * 128
        raw = json.dumps(value).encode()
        ref["digest"] = hashlib.sha256(raw).hexdigest()
        ref["size_bytes"] = len(raw)
        (bundle.receipts / f"{ref['digest']}.json").write_bytes(raw)
    code, report = bundle.run()
    assert code == 1
    assert reason in reasons(report)


@pytest.mark.parametrize(
    "location", ["manifest", "scenario", "binding", "receipt", "trust", "target"]
)
def test_raw_canary_and_unknown_keys_are_rejected_without_echo(bundle, location):
    if location == "receipt":
        ref = bundle.ref("happy.authenticated_login")
        raw = json.loads((bundle.receipts / f"{ref['digest']}.json").read_text())
        raw[CANARY] = CANARY
        data = json.dumps(raw).encode()
        ref.update(digest=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
        (bundle.receipts / f"{ref['digest']}.json").write_bytes(data)
    else:
        destination = {
            "manifest": bundle.manifest,
            "scenario": bundle.scenario("happy"),
            "binding": bundle.scenario("happy")["binding"],
            "trust": bundle.trust,
            "target": bundle.target,
        }[location]
        destination[CANARY] = CANARY
    code, report = bundle.run()
    assert code != 0
    assert "invalid_record" in reasons(report)


@pytest.mark.parametrize(
    "value",
    [
        CANARY,
        "123456789012",
        "user@example.invalid",
        "https://private.invalid/result?token=secret",
        "arn:aws:s3:::private",
        "../../private",
        "r-" + "a" * 32 + "\n",
    ],
)
def test_public_identifiers_are_conservative_and_diagnostics_do_not_echo(bundle, value):
    bundle.manifest["campaign_id"] = value
    code, report = bundle.run()
    assert code == 2
    assert "invalid_record" in reasons(report)


@pytest.mark.parametrize("mutation", ["revoked", "expired", "unknown"])
def test_collector_revocation_expiry_and_unknown_keys_fail(bundle, mutation):
    collector = next(c for c in bundle.trust["collectors"] if c["source"] == "browser")
    if mutation == "revoked":
        collector["revoked"] = True
    elif mutation == "expired":
        collector["valid_until"] = bundle.now - 120
    else:
        collector["key_id"] = alias("unknown-key")
    code, report = bundle.run()
    assert code == 1
    assert reasons(report) & {"untrusted_collector", "collector_expired"}


def test_stale_campaign_and_receipt_timestamp_fail(bundle):
    bundle.manifest["started_at"] -= 100_000
    bundle.manifest["finished_at"] -= 100_000
    code, report = bundle.run()
    assert code == 1
    assert {"stale_or_future_campaign", "observation_outside_campaign"} <= reasons(report)


@pytest.mark.parametrize("field,value", [("observed", 0), ("violations", 1), ("observed", 200)])
def test_fixed_expectation_and_measured_violations_are_not_self_declared_pass(bundle, field, value):
    check = "happy.asynchronous_202"
    observation = bundle.observation(check)
    observation[field] = value
    bundle.save_receipt(check, observation)
    code, report = bundle.run()
    assert code == 1
    assert "observation_not_passed" in reasons(report)


def test_json_schema_is_reproducible_and_validates_synthetic_bundle(bundle):
    schema = json.loads((ROOT / "schemas/rehearsal-v1.json").read_text())
    assert schema == rehearsal_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(bundle.manifest, schema)
    parsed(RehearsalManifest, bundle.manifest)
    changed = copy.deepcopy(bundle.manifest)
    changed["scenarios"][0]["evidence"][0]["check"] = "unknown.check"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(changed, schema)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_scenario",
        "duplicate_check",
        "reused_receipt",
        "wrong_scenario",
        "unknown_check",
        "reused_human_revision",
        "reused_attempt",
        "wrong_worker_run",
        "failure_output",
        "missing_task",
        "same_rollback_image",
        "output_run",
    ],
)
def test_invalid_relations_fail_closed_at_input(bundle, mutation):
    happy = bundle.scenario("happy")
    human = bundle.scenario("human")
    worker = bundle.scenario("worker_recovery")
    if mutation == "duplicate_scenario":
        bundle.manifest["scenarios"][1] = copy.deepcopy(happy)
    elif mutation == "duplicate_check":
        happy["evidence"].append(copy.deepcopy(happy["evidence"][0]))
    elif mutation == "reused_receipt":
        happy["evidence"][1]["digest"] = happy["evidence"][0]["digest"]
    elif mutation == "wrong_scenario":
        happy["evidence"][0]["check"] = "human.waiting_persisted"
    elif mutation == "unknown_check":
        happy["evidence"][0]["check"] = "unknown.check"
    elif mutation == "reused_human_revision":
        human["previous"]["revision_digest"] = human["binding"]["revision_digest"]
    elif mutation == "reused_attempt":
        worker["previous"]["correlation"]["attempt_id"] = worker["binding"]["correlation"][
            "attempt_id"
        ]
    elif mutation == "wrong_worker_run":
        worker["previous"]["correlation"]["run_id"] = alias("wrong-run")
    elif mutation == "failure_output":
        bundle.scenario("failure")["binding"] = copy.deepcopy(happy["binding"])
    elif mutation == "missing_task":
        human["task_id"] = None
    elif mutation == "same_rollback_image":
        bundle.scenario("rollback")["rollback_image_digest"] = bundle.target["image_digest"]
    else:
        happy["binding"]["output"]["run_id"] = alias("wrong-output-run")
    code, report = bundle.run()
    assert code == 2
    assert "invalid_record" in reasons(report)


def test_argument_errors_do_not_echo_paths_or_private_values(bundle):
    code, report = bundle.run(command=[sys.executable, str(SCRIPT), "--" + CANARY])
    assert code == 2
    assert "invalid_arguments" in reasons(report)


@pytest.mark.parametrize("mutation", ["duplicate", "malformed", "oversized", "symlink"])
def test_hostile_file_inputs_fail_without_raw_errors(bundle, mutation):
    bundle.write()
    path = bundle.path / "manifest.json"
    if mutation == "duplicate":
        path.write_text('{"schema_version":"rehearsal-v1","schema_version":"' + CANARY + '"}')
    elif mutation == "malformed":
        path.write_bytes(CANARY.encode() + b"\xff")
    elif mutation == "oversized":
        path.write_bytes(b" " * 1_048_577)
    else:
        path.unlink()
        path.symlink_to(bundle.path / "target.json")
    code, report = bundle.run(write=False)
    assert code == 2
    assert reasons(report) & {
        "duplicate_json_key",
        "invalid_record",
        "invalid_file",
        "unreadable_file",
    }


def test_strict_numbers_reject_booleans_and_numeric_strings(bundle):
    for value in (True, "256", 256.0):
        bundle.scenario("happy")["binding"]["output"]["size_bytes"] = value
        with pytest.raises(ValidationError):
            parsed(RehearsalManifest, bundle.manifest)
