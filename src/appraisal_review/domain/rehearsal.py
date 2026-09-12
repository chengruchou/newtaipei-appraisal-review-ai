"""Value-free acceptance evidence, independent of production service contracts.

These records describe observations of existing boundaries, never authorize a
review or supply a production provider. Signed observations still require an
independently provisioned collector trust root. See docs/cloud-acceptance.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RehearsalDigest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", max_length=64)]
RehearsalCommit = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$", max_length=40)]
# Random aliases only. Domain labels, account IDs, filenames and URLs are forbidden.
RehearsalID = Annotated[str, Field(pattern=r"^r-[a-f0-9]{32}$", max_length=34)]
RehearsalSource = Literal["browser", "network", "aws", "local", "ci", "human"]
RehearsalStatus = Literal["passed", "failed", "skipped", "not_run", "unknown"]
RehearsalMode = Literal["synthetic", "live_sandbox"]
RehearsalScenarioName = Literal[
    "happy",
    "human",
    "idempotency",
    "worker_recovery",
    "outbox_recovery",
    "failure",
    "dlq",
    "authorization",
    "privacy",
    "output",
    "rollback",
    "cleanup",
    "observability",
    "integrations",
    "ci",
    "human_acceptance",
]


@dataclass(frozen=True)
class RehearsalCheckSpec:
    scenario: RehearsalScenarioName
    source: RehearsalSource
    expected: int = 1
    output_required: bool = False


def _checks() -> dict[str, RehearsalCheckSpec]:
    """Every named probe is mandatory; 1 means the specified invariant was observed."""
    groups: tuple[tuple[RehearsalScenarioName, RehearsalSource, str], ...] = (
        ("happy", "browser", "authenticated_login local_export_confirmation authorized_download"),
        ("happy", "network", "sanitized_upload asynchronous_202"),
        ("happy", "aws", "runtime_model deterministic_review pdf_written fenced_manifest"),
        ("human", "aws", "waiting_persisted attempt_released recomputed published"),
        (
            "human",
            "browser",
            "exact_source_and_sides raw_confidence_preserved response_new_revision",
        ),
        ("human", "network", "authenticated_response stale_confirmation_rejected"),
        ("idempotency", "network", "same_key_same_payload same_key_changed_payload_rejected"),
        ("idempotency", "browser", "timeout_resends_confirmed_command"),
        ("idempotency", "aws", "one_job_one_completion"),
        ("worker_recovery", "aws", "heartbeat_stopped lease_expired new_worker old_attempt_fenced"),
        ("worker_recovery", "browser", "recovered_result"),
        ("outbox_recovery", "aws", "committed_unsent restart_reconciled single_completion"),
        ("outbox_recovery", "browser", "recovered_result"),
        (
            "failure",
            "aws",
            "invalid_pdf model_timeout model_throttle runtime_error "
            "pdf_gate max_attempts no_output",
        ),
        ("failure", "browser", "findings_retained no_completed_form"),
        ("dlq", "aws", "message_arrived alarm_fired bounded_redrive_or_termination"),
        ("dlq", "browser", "terminal_state"),
        (
            "authorization",
            "network",
            "cross_principal cross_case cross_job cross_document cross_task cross_artifact "
            "old_revision revoked_grant replaced_source_version",
        ),
        ("authorization", "aws", "runtime_cross_resource_denied"),
        (
            "privacy",
            "local",
            "canary_in_original canary_in_filename canary_in_mapping canary_in_key "
            "canary_in_rehydrated_pdf rehydration_only_local",
        ),
        (
            "privacy",
            "network",
            "capture_complete original_absent filename_absent mapping_absent "
            "key_absent rehydrated_absent",
        ),
        (
            "privacy",
            "aws",
            "inventory_complete s3_clean dynamodb_clean sqs_clean dlq_clean "
            "logs_clean model_request_clean",
        ),
        ("output", "browser", "download_reopened"),
        (
            "output",
            "local",
            "formal_template_golden field_map_complete original_unchanged "
            "downloaded_hash_size_version",
        ),
        ("output", "aws", "manifest_run_version_link"),
        ("rollback", "aws", "preflight_identity_owned previous_image_restored health_and_invoke"),
        ("rollback", "browser", "restored_flow"),
        (
            "cleanup",
            "aws",
            "exact_inventory scoped_deletion no_unexpected_survivors retained_audit",
        ),
        (
            "observability",
            "aws",
            "dashboard_queries queue_age job_latency success_failure retry waiting_tasks "
            "expired_leases dlq_depth model_latency usage_cost_proxy "
            "alarm_owner_threshold_runbook alarm_test",
        ),
        ("integrations", "ci", "pr34 pr35 pr36 pr37 pr38 pr39 production_composition"),
        (
            "ci",
            "ci",
            "branch_policy lint format types tests goldens offline_cloud iac schema "
            "http_smoke invocation_smoke container_arm64 image_scan dependency_scan frontend",
        ),
        ("human_acceptance", "human", "independent_reviewer"),
    )
    output_checks = {
        "happy.authorized_download",
        "happy.pdf_written",
        "happy.fenced_manifest",
        "human.published",
        "worker_recovery.recovered_result",
        "outbox_recovery.recovered_result",
        "output.download_reopened",
        "output.formal_template_golden",
        "output.field_map_complete",
        "output.downloaded_hash_size_version",
        "output.manifest_run_version_link",
    }
    result = {}
    for scenario, source, names in groups:
        for name in names.split():
            key = f"{scenario}.{name}"
            result[key] = RehearsalCheckSpec(
                scenario,
                source,
                202 if key == "happy.asynchronous_202" else 1,
                key in output_checks,
            )
    return result


REHEARSAL_CHECKS = _checks()
REHEARSAL_SCENARIOS = tuple(dict.fromkeys(spec.scenario for spec in REHEARSAL_CHECKS.values()))
CheckName = Annotated[str, Field(json_schema_extra={"enum": list(REHEARSAL_CHECKS)})]
MAX_REHEARSAL_BYTES = 1_048_576


class RehearsalModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        revalidate_instances="always",
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


class RehearsalTarget(RehearsalModel):
    code_commit: RehearsalCommit
    pr_head_commit: RehearsalCommit
    pr_base_commit: RehearsalCommit
    pull_request_number: int = Field(gt=0, le=1_000_000_000)
    ci_tested_commit: RehearsalCommit
    ci_namespace: Literal["issue31-exact-revision-v1"]
    ci_run_digest: RehearsalDigest
    ci_run_attempt: int = Field(gt=0, le=1_000_000)
    image_digest: RehearsalDigest
    model_digest: RehearsalDigest
    prompt_digest: RehearsalDigest
    deployment_digest: RehearsalDigest
    config_digest: RehearsalDigest
    parser_digest: RehearsalDigest
    normalizer_digest: RehearsalDigest
    ci_workflow_digest: RehearsalDigest

    @model_validator(mode="after")
    def exact_ci(self) -> RehearsalTarget:
        if self.code_commit != self.ci_tested_commit:
            raise ValueError("ci_did_not_test_deployed_commit")
        return self


class RehearsalCorrelation(RehearsalModel):
    case_id: RehearsalID
    job_id: RehearsalID
    run_id: RehearsalID
    attempt_id: RehearsalID
    trace_id: RehearsalID
    revision_id: RehearsalID


class RehearsalOutput(RehearsalModel):
    artifact_id: RehearsalID
    content_digest: RehearsalDigest
    size_bytes: int = Field(gt=0, le=1_073_741_824)
    object_version_digest: RehearsalDigest
    manifest_digest: RehearsalDigest
    manifest_version: int = Field(gt=0)
    run_id: RehearsalID
    attempt_id: RehearsalID
    revision_digest: RehearsalDigest
    template_digest: RehearsalDigest
    field_map_digest: RehearsalDigest


class RehearsalBinding(RehearsalModel):
    correlation: RehearsalCorrelation
    source_digest: RehearsalDigest
    source_version_digest: RehearsalDigest
    source_snapshot_digest: RehearsalDigest
    privacy_attestation_digest: RehearsalDigest
    input_digest: RehearsalDigest
    decision_digest: RehearsalDigest
    revision_digest: RehearsalDigest
    material_digest: RehearsalDigest
    confirmation_digest: RehearsalDigest
    approval_digest: RehearsalDigest
    rules_digest: RehearsalDigest
    template_digest: RehearsalDigest
    field_map_digest: RehearsalDigest
    golden_digest: RehearsalDigest
    output: RehearsalOutput | None

    @model_validator(mode="after")
    def output_link(self) -> RehearsalBinding:
        if self.output and (
            self.output.run_id != self.correlation.run_id
            or self.output.attempt_id != self.correlation.attempt_id
            or self.output.revision_digest != self.revision_digest
            or self.output.template_digest != self.template_digest
            or self.output.field_map_digest != self.field_map_digest
        ):
            raise ValueError("output_binding_mismatch")
        return self


class RehearsalEvidenceRef(RehearsalModel):
    check: CheckName
    digest: RehearsalDigest
    size_bytes: int = Field(gt=0, le=MAX_REHEARSAL_BYTES)

    @model_validator(mode="after")
    def known_check(self) -> RehearsalEvidenceRef:
        if self.check not in REHEARSAL_CHECKS:
            raise ValueError("unknown_check")
        return self


class RehearsalScenario(RehearsalModel):
    name: RehearsalScenarioName
    status: RehearsalStatus
    binding: RehearsalBinding | None
    previous: RehearsalBinding | None = None
    task_id: RehearsalID | None = None
    rollback_image_digest: RehearsalDigest | None = None
    evidence: tuple[RehearsalEvidenceRef, ...] = Field(max_length=200)

    @model_validator(mode="after")
    def links(self) -> RehearsalScenario:
        if len({ref.check for ref in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate_check")
        if any(REHEARSAL_CHECKS[ref.check].scenario != self.name for ref in self.evidence):
            raise ValueError("wrong_scenario")
        if self.previous and self.name not in {"human", "worker_recovery"}:
            raise ValueError("unexpected_previous_binding")
        if self.status != "passed":
            return self
        if self.binding is None:
            raise ValueError("missing_binding")
        if (
            self.name in {"happy", "human", "worker_recovery", "outbox_recovery", "output"}
            and self.binding.output is None
        ):
            raise ValueError("missing_output")
        if self.name in {"failure", "dlq"} and self.binding.output is not None:
            raise ValueError("failure_output")
        if self.name in {"human", "worker_recovery"}:
            old, new = self.previous, self.binding
            if old is None or old.correlation.job_id != new.correlation.job_id:
                raise ValueError("missing_previous_job")
            if old.correlation.case_id != new.correlation.case_id:
                raise ValueError("previous_case_mismatch")
            if old.correlation.attempt_id == new.correlation.attempt_id:
                raise ValueError("attempt_reused")
            if self.name == "human":
                if self.task_id is None or old.output is not None:
                    raise ValueError("human_task_binding")
                if (
                    old.correlation.run_id == new.correlation.run_id
                    or old.correlation.revision_id == new.correlation.revision_id
                    or any(
                        getattr(old, field) == getattr(new, field)
                        for field in (
                            "revision_digest",
                            "material_digest",
                            "confirmation_digest",
                            "approval_digest",
                        )
                    )
                ):
                    raise ValueError("human_revision_reused")
            elif (
                old.correlation.run_id != new.correlation.run_id
                or old.correlation.revision_id != new.correlation.revision_id
                or old.revision_digest != new.revision_digest
                or old.material_digest != new.material_digest
            ):
                raise ValueError("worker_run_mismatch")
        if self.name == "rollback" and self.rollback_image_digest is None:
            raise ValueError("missing_rollback_image")
        return self


class RehearsalManifest(RehearsalModel):
    schema_version: Literal["rehearsal-v1"]
    data_classification: Literal["synthetic_cases_only"]
    mode: RehearsalMode
    campaign_id: RehearsalID
    started_at: int = Field(gt=0)
    finished_at: int = Field(gt=0)
    target: RehearsalTarget
    scenarios: tuple[RehearsalScenario, ...] = Field(max_length=len(REHEARSAL_SCENARIOS))

    @model_validator(mode="after")
    def unique_scenarios(self) -> RehearsalManifest:
        if self.finished_at < self.started_at or self.finished_at - self.started_at > 86_400:
            raise ValueError("invalid_campaign_window")
        if len({s.name for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("duplicate_scenario")
        refs = [r.digest for s in self.scenarios for r in s.evidence]
        if len(set(refs)) != len(refs):
            raise ValueError("reused_receipt")
        for scenario in self.scenarios:
            if scenario.rollback_image_digest == self.target.image_digest:
                raise ValueError("rollback_image_unchanged")
        return self


class RehearsalObservation(RehearsalModel):
    schema_version: Literal["rehearsal-observation-v1"]
    campaign_id: RehearsalID
    mode: RehearsalMode
    check: CheckName
    source: RehearsalSource
    target_digest: RehearsalDigest
    context_digest: RehearsalDigest
    correlation: RehearsalCorrelation
    observed_at: int = Field(gt=0)
    status: RehearsalStatus
    # No caller-selected expected value. The fixed catalog owns the invariant.
    observed: int = Field(ge=0, le=1_000_000_000)
    sample_count: int = Field(gt=0, le=1_000_000_000)
    violations: int = Field(ge=0, le=1_000_000_000)
    capture_digest: RehearsalDigest
    capture_size_bytes: int = Field(gt=0, le=1_073_741_824)
    capture_version_digest: RehearsalDigest
    output: RehearsalOutput | None

    @model_validator(mode="after")
    def measured_check(self) -> RehearsalObservation:
        if self.check not in REHEARSAL_CHECKS or self.violations > self.sample_count:
            raise ValueError("invalid_observation")
        return self


class RehearsalReceipt(RehearsalModel):
    key_id: RehearsalID
    observation: RehearsalObservation
    signature: Annotated[str, Field(pattern=r"^[a-f0-9]{128}$", max_length=128)]


class RehearsalCollector(RehearsalModel):
    key_id: RehearsalID
    public_key: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", max_length=64)]
    source: RehearsalSource
    mode: RehearsalMode
    valid_from: int = Field(gt=0)
    valid_until: int = Field(gt=0)
    revoked: bool


class RehearsalTrust(RehearsalModel):
    schema_version: Literal["rehearsal-trust-v1"]
    collectors: tuple[RehearsalCollector, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_keys(self) -> RehearsalTrust:
        if len({c.key_id for c in self.collectors}) != len(self.collectors):
            raise ValueError("duplicate_collector")
        if len({c.public_key for c in self.collectors}) != len(self.collectors):
            raise ValueError("shared_collector_key")
        if any(c.valid_until <= c.valid_from for c in self.collectors):
            raise ValueError("invalid_collector_window")
        return self


def rehearsal_canonical(value: BaseModel) -> bytes:
    """Canonical UTF-8 JSON, including defaults and nulls; integers only."""
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def rehearsal_digest(value: BaseModel) -> str:
    return hashlib.sha256(rehearsal_canonical(value)).hexdigest()


def rehearsal_context(scenario: RehearsalScenario) -> str:
    # References/signatures cannot be included in their own signed context.
    return rehearsal_digest(scenario.model_copy(update={"evidence": ()}))


def rehearsal_schema() -> dict[str, object]:
    schema = RehearsalManifest.model_json_schema()
    # Standalone definitions also describe files read by the CLI.
    for model in (RehearsalReceipt, RehearsalTrust):
        extra = model.model_json_schema()
        schema.setdefault("$defs", {}).update(extra.pop("$defs", {}))
        schema["$defs"][model.__name__] = extra
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema
