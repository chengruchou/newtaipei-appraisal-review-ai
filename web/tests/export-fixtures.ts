/** Canonical-shape export contract inputs for unit regression only, not real case data. */
import type {
  CreateExportCommand,
  ExportArtifact,
  ExportOperation,
  ExportReadiness,
  ExportReadinessBlocker,
  ExportRunReference,
  ReportApproval,
  ServerExportBasis,
} from "@/api/exports";
import type { ExportBasis } from "@/features/ExportPanel";

export const SNAPSHOT_DIGEST = "b".repeat(64);

export const RUN: ExportRunReference = {
  schema_version: "service-v1",
  run_id: "33333333-3333-3333-3333-333333333333",
  revision: {
    schema_version: "service-v1",
    case_id: "case-1",
    revision_id: "r1",
    material_digest: "a".repeat(64),
  },
  attempt_id: null,
  runtime_session_id: null,
};

export function basis(): ExportBasis {
  return {
    run: RUN,
    calculationSnapshotDigest: SNAPSHOT_DIGEST,
    templateBundle: {
      bundle_id: "official-tables",
      version: "2026.09",
      bundle_hash: "c".repeat(64),
    },
  };
}

export function command(overrides: Partial<CreateExportCommand> = {}): CreateExportCommand {
  const base = basis();
  return {
    schema_version: "service-v1",
    idempotency_key: "wb-test-key",
    run: base.run,
    calculation_snapshot_digest: base.calculationSnapshotDigest,
    template_bundle: base.templateBundle,
    requested_mode: "draft",
    export_format: "xlsx",
    ...overrides,
  };
}

export const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export function artifact(overrides: Partial<ExportArtifact> = {}): ExportArtifact {
  return {
    artifact_id: "art-1",
    kind: "official_workbook",
    content_type: XLSX_TYPE,
    filename: "表3_地價區段勘查表.xlsx",
    size_bytes: 1024,
    content_hash: "d".repeat(64),
    ...overrides,
  };
}

export function readinessBlocker(
  overrides: Partial<ExportReadinessBlocker> = {},
): ExportReadinessBlocker {
  return {
    code: "missing_condition",
    message: "表4 R-01 尚未確認交易條件",
    source_key: "table_4:R-01:condition",
    table: "table_4",
    subject_id: "R-01",
    current_state: "missing",
    needed: "已確認的交易條件值",
    action: "請於人工作業完成確認",
    ...overrides,
  };
}

export function readiness(overrides: Partial<ExportReadiness> = {}): ExportReadiness {
  return {
    policy_version: "readiness-v1",
    state: "ready_to_submit",
    blockers: [],
    required_total: 5,
    required_satisfied: 5,
    ...overrides,
  };
}

export function approval(overrides: Partial<ReportApproval> = {}): ReportApproval {
  return {
    approval_id: "appr-1",
    job_id: "job-1",
    run: RUN,
    binding: {
      calculation_snapshot_digest: SNAPSHOT_DIGEST,
      template_bundle: basis().templateBundle,
      workbook_hashes: {
        table_3: "e".repeat(64),
        table_4: "f".repeat(64),
        table_5: "0".repeat(64),
      },
      readiness_policy_version: "readiness-v1",
    },
    status: "submitted",
    submitted_by: { actor_id: "reviewer-1", kind: "human" },
    submitted_at: 1_757_600_000,
    payload_digest: "1".repeat(64),
    decision: null,
    ...overrides,
  };
}

export function serverBasis(overrides: Partial<ServerExportBasis> = {}): ServerExportBasis {
  const base = basis();
  return {
    job_id: "job-1",
    run: base.run,
    calculation_snapshot_digest: base.calculationSnapshotDigest,
    template_bundle: base.templateBundle,
    blockers: [],
    readiness: readiness(),
    approval: null,
    ...overrides,
  };
}

export function operation(overrides: Partial<ExportOperation> = {}): ExportOperation {
  return {
    export_id: "exp-1",
    job_id: "job-1",
    export_format: "xlsx",
    requested_mode: "draft",
    effective_mode: "draft",
    status: "queued",
    tables: [],
    artifacts: [],
    blockers: [],
    problem: null,
    ...overrides,
  };
}
