/** Canonical-shape export contract inputs for unit regression only, not real case data. */
import type {
  CreateExportCommand,
  ExportArtifact,
  ExportOperation,
  ExportRunReference,
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
