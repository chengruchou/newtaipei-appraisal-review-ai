import { TransportError, UNKNOWN_OUTCOME, type ServiceErrorCode } from "./problems";

/**
 * Hand-written client extension for the official-tables export routes.
 *
 * The service routes are being added by the integrator; these types mirror the agreed
 * contract and are replaced by the regenerated OpenAPI schema once it lands. Because the
 * generated validators cannot know these shapes yet, this module carries its own
 * structural checks, with the same rule as the main client: only the service's canonical
 * problem envelope is a decision, everything else is an unknown outcome.
 */

export type ExportFormat = "pdf" | "xlsx";
export type ExportMode = "draft" | "formal";
export type ExportStatus = "queued" | "running" | "succeeded" | "failed" | "partial";
export type ExportTableId = "table_3" | "table_4" | "table_5";
export type ExportArtifactKind = "official_workbook" | "converted_pdf";

export interface ExportRevisionReference {
  schema_version: "service-v1";
  case_id: string;
  revision_id: string;
  material_digest: string;
}

export interface ExportRunReference {
  schema_version: "service-v1";
  run_id: string;
  revision: ExportRevisionReference;
  attempt_id?: string | null;
  runtime_session_id?: string | null;
}

export interface TemplateBundleReference {
  bundle_id: string;
  version: string;
  bundle_hash: string;
}

export interface CreateExportCommand {
  schema_version: "service-v1";
  idempotency_key: string;
  run: ExportRunReference;
  calculation_snapshot_digest: string;
  template_bundle: TemplateBundleReference;
  requested_mode: ExportMode;
  export_format: ExportFormat;
}

export interface ExportTableProblem {
  code?: string | null;
  message?: string | null;
}

export interface ExportTableDelivery {
  table: ExportTableId;
  delivered: boolean;
  artifact_id?: string | null;
  problem?: ExportTableProblem | null;
}

export interface ExportArtifact {
  artifact_id: string;
  kind: ExportArtifactKind;
  content_type: string;
  filename: string;
  size_bytes: number;
  content_hash: string;
}

export interface ExportOperation {
  export_id: string;
  job_id: string;
  /** Immutable for the lifetime of the operation; a new format is a new operation. */
  export_format: ExportFormat;
  requested_mode: ExportMode;
  effective_mode: ExportMode;
  status: ExportStatus;
  tables: ExportTableDelivery[];
  artifacts: ExportArtifact[];
  blockers: string[];
  problem?: ExportTableProblem | null;
}

export interface ExportDownload {
  bytes: ArrayBuffer;
  filename: string;
  contentType: string;
}

/* ------------------------------------------------------------------------- *
 * Formal report approval (report-approvals routes)
 * ------------------------------------------------------------------------- */

export type ExportReadinessState = "pending_data" | "ready_to_submit";

export interface ExportReadinessBlocker {
  code: string;
  message: string;
  source_key?: string | null;
  table?: ExportTableId | null;
  subject_id?: string | null;
  current_state?: string | null;
  needed: string;
  action: string;
}

export interface ExportReadiness {
  policy_version: string;
  state: ExportReadinessState;
  blockers: ExportReadinessBlocker[];
  required_total: number;
  required_satisfied: number;
}

export type ReportApprovalStatus =
  "submitted" | "approved" | "returned" | "withdrawn" | "superseded";

export type ApprovalDecisionKind = "approve" | "return" | "withdraw";

export interface ReportApprovalActor {
  actor_id: string;
  kind: string;
}

export interface ReportApprovalBinding {
  calculation_snapshot_digest: string;
  template_bundle: TemplateBundleReference;
  workbook_hashes: Record<ExportTableId, string>;
  readiness_policy_version: string;
}

export interface ReportApprovalDecision {
  decision: ApprovalDecisionKind;
  actor: ReportApprovalActor;
  decided_at: number;
  reason?: string | null;
}

export interface ReportApproval {
  approval_id: string;
  job_id: string;
  run: ExportRunReference;
  binding: ReportApprovalBinding;
  status: ReportApprovalStatus;
  submitted_by: ReportApprovalActor;
  /** Server clock, in seconds since the epoch. */
  submitted_at: number;
  payload_digest: string;
  decision?: ReportApprovalDecision | null;
}

export interface SubmitApprovalCommand {
  schema_version: "service-v1";
  idempotency_key: string;
  run: ExportRunReference;
  calculation_snapshot_digest: string;
  template_bundle: TemplateBundleReference;
}

export interface ApprovalDecisionCommand {
  schema_version: "service-v1";
  idempotency_key: string;
  decision: ApprovalDecisionKind;
  /** Required by the service for "return" and "withdraw". */
  reason?: string;
}

/**
 * A definitive refusal from the service, carrying the service's own sentence when one was
 * sent, so a 503 can show what the deployment itself says rather than a mock.
 */
export class ExportServiceError extends Error {
  readonly code: ServiceErrorCode;
  readonly status: number;
  readonly serviceMessage: string | null;

  constructor(code: ServiceErrorCode, status: number, serviceMessage: string | null = null) {
    super(code);
    this.name = "ExportServiceError";
    this.code = code;
    this.status = status;
    this.serviceMessage = serviceMessage;
  }
}

const KNOWN_CODES: readonly ServiceErrorCode[] = [
  "invalid_request",
  "unauthorized",
  "not_found",
  "version_conflict",
  "capability_unavailable",
  "execution_failed",
];

/** 401 (no valid session) and 403 (no permission) both surface as unauthorized. */
const EXPECTED_STATUS: Record<number, ServiceErrorCode> = {
  401: "unauthorized",
  403: "unauthorized",
  404: "not_found",
  409: "version_conflict",
  422: "invalid_request",
  500: "execution_failed",
  503: "capability_unavailable",
};

function canonicalExportProblem(status: number, body: unknown): ExportServiceError | null {
  const expected = EXPECTED_STATUS[status];
  if (!expected || !isRecord(body) || typeof body.code !== "string") return null;
  if (!(KNOWN_CODES as readonly string[]).includes(body.code) || body.code !== expected)
    return null;
  const message =
    typeof body.message === "string" && body.message.trim() ? body.message.trim() : null;
  return new ExportServiceError(expected, status, message);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const FORMATS: readonly string[] = ["pdf", "xlsx"];
const MODES: readonly string[] = ["draft", "formal"];
const STATUSES: readonly string[] = ["queued", "running", "succeeded", "failed", "partial"];
const TABLES: readonly string[] = ["table_3", "table_4", "table_5"];
const KINDS: readonly string[] = ["official_workbook", "converted_pdf"];

const MALFORMED = "The service response could not be read or validated.";

function parseTable(value: unknown): ExportTableDelivery {
  if (
    !isRecord(value) ||
    typeof value.table !== "string" ||
    !TABLES.includes(value.table) ||
    typeof value.delivered !== "boolean"
  )
    throw new TransportError(MALFORMED);
  return {
    table: value.table as ExportTableId,
    delivered: value.delivered,
    artifact_id: typeof value.artifact_id === "string" ? value.artifact_id : null,
    problem: isRecord(value.problem)
      ? {
          code: typeof value.problem.code === "string" ? value.problem.code : null,
          message: typeof value.problem.message === "string" ? value.problem.message : null,
        }
      : null,
  };
}

function parseArtifact(value: unknown): ExportArtifact {
  if (
    !isRecord(value) ||
    typeof value.artifact_id !== "string" ||
    typeof value.kind !== "string" ||
    !KINDS.includes(value.kind) ||
    typeof value.content_type !== "string" ||
    typeof value.filename !== "string" ||
    typeof value.content_hash !== "string"
  )
    throw new TransportError(MALFORMED);
  return {
    artifact_id: value.artifact_id,
    kind: value.kind as ExportArtifactKind,
    content_type: value.content_type,
    filename: value.filename,
    size_bytes: typeof value.size_bytes === "number" ? value.size_bytes : 0,
    content_hash: value.content_hash,
  };
}

export function parseExportOperation(payload: unknown): ExportOperation {
  if (
    !isRecord(payload) ||
    typeof payload.export_id !== "string" ||
    typeof payload.job_id !== "string" ||
    typeof payload.export_format !== "string" ||
    !FORMATS.includes(payload.export_format) ||
    typeof payload.requested_mode !== "string" ||
    !MODES.includes(payload.requested_mode) ||
    typeof payload.effective_mode !== "string" ||
    !MODES.includes(payload.effective_mode) ||
    typeof payload.status !== "string" ||
    !STATUSES.includes(payload.status)
  )
    throw new TransportError(MALFORMED);
  const tables = Array.isArray(payload.tables) ? payload.tables.map(parseTable) : [];
  const artifacts = Array.isArray(payload.artifacts) ? payload.artifacts.map(parseArtifact) : [];
  const blockers = Array.isArray(payload.blockers)
    ? payload.blockers.filter((entry): entry is string => typeof entry === "string")
    : [];
  return {
    export_id: payload.export_id,
    job_id: payload.job_id,
    export_format: payload.export_format as ExportFormat,
    requested_mode: payload.requested_mode as ExportMode,
    effective_mode: payload.effective_mode as ExportMode,
    status: payload.status as ExportStatus,
    tables,
    artifacts,
    blockers,
    problem: isRecord(payload.problem)
      ? {
          code: typeof payload.problem.code === "string" ? payload.problem.code : null,
          message: typeof payload.problem.message === "string" ? payload.problem.message : null,
        }
      : null,
  };
}

const READINESS_STATES: readonly string[] = ["pending_data", "ready_to_submit"];
const APPROVAL_STATUSES: readonly string[] = [
  "submitted",
  "approved",
  "returned",
  "withdrawn",
  "superseded",
];
const DECISION_KINDS: readonly string[] = ["approve", "return", "withdraw"];

function optionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function parseReadinessBlocker(value: unknown): ExportReadinessBlocker {
  if (
    !isRecord(value) ||
    typeof value.code !== "string" ||
    typeof value.message !== "string" ||
    typeof value.needed !== "string" ||
    typeof value.action !== "string"
  )
    throw new TransportError(MALFORMED);
  const table =
    typeof value.table === "string" && TABLES.includes(value.table) ? value.table : null;
  return {
    code: value.code,
    message: value.message,
    source_key: optionalString(value.source_key),
    table: table as ExportTableId | null,
    subject_id: optionalString(value.subject_id),
    current_state: optionalString(value.current_state),
    needed: value.needed,
    action: value.action,
  };
}

export function parseExportReadiness(payload: unknown): ExportReadiness {
  if (
    !isRecord(payload) ||
    typeof payload.policy_version !== "string" ||
    typeof payload.state !== "string" ||
    !READINESS_STATES.includes(payload.state) ||
    typeof payload.required_total !== "number" ||
    typeof payload.required_satisfied !== "number"
  )
    throw new TransportError(MALFORMED);
  return {
    policy_version: payload.policy_version,
    state: payload.state as ExportReadinessState,
    blockers: Array.isArray(payload.blockers) ? payload.blockers.map(parseReadinessBlocker) : [],
    required_total: payload.required_total,
    required_satisfied: payload.required_satisfied,
  };
}

function parseActor(value: unknown): ReportApprovalActor {
  if (!isRecord(value) || typeof value.actor_id !== "string" || typeof value.kind !== "string")
    throw new TransportError(MALFORMED);
  return { actor_id: value.actor_id, kind: value.kind };
}

function parseApprovalBinding(value: unknown): ReportApprovalBinding {
  if (
    !isRecord(value) ||
    typeof value.calculation_snapshot_digest !== "string" ||
    !isRecord(value.template_bundle) ||
    !isRecord(value.workbook_hashes) ||
    typeof value.readiness_policy_version !== "string"
  )
    throw new TransportError(MALFORMED);
  const hashes: Partial<Record<ExportTableId, string>> = {};
  for (const table of TABLES) {
    const hash = value.workbook_hashes[table];
    if (typeof hash !== "string") throw new TransportError(MALFORMED);
    hashes[table as ExportTableId] = hash;
  }
  return {
    calculation_snapshot_digest: value.calculation_snapshot_digest,
    template_bundle: value.template_bundle as unknown as TemplateBundleReference,
    workbook_hashes: hashes as Record<ExportTableId, string>,
    readiness_policy_version: value.readiness_policy_version,
  };
}

export function parseReportApproval(payload: unknown): ReportApproval {
  if (
    !isRecord(payload) ||
    typeof payload.approval_id !== "string" ||
    typeof payload.job_id !== "string" ||
    !isRecord(payload.run) ||
    typeof payload.status !== "string" ||
    !APPROVAL_STATUSES.includes(payload.status) ||
    typeof payload.submitted_at !== "number" ||
    typeof payload.payload_digest !== "string"
  )
    throw new TransportError(MALFORMED);
  let decision: ReportApprovalDecision | null = null;
  if (isRecord(payload.decision)) {
    const raw = payload.decision;
    if (
      typeof raw.decision !== "string" ||
      !DECISION_KINDS.includes(raw.decision) ||
      typeof raw.decided_at !== "number"
    )
      throw new TransportError(MALFORMED);
    decision = {
      decision: raw.decision as ApprovalDecisionKind,
      actor: parseActor(raw.actor),
      decided_at: raw.decided_at,
      reason: optionalString(raw.reason),
    };
  }
  return {
    approval_id: payload.approval_id,
    job_id: payload.job_id,
    run: payload.run as unknown as ExportRunReference,
    binding: parseApprovalBinding(payload.binding),
    status: payload.status as ReportApprovalStatus,
    submitted_by: parseActor(payload.submitted_by),
    submitted_at: payload.submitted_at,
    payload_digest: payload.payload_digest,
    decision,
  };
}

export interface ExportsClientOptions {
  baseUrl: string;
  token: () => Promise<string | null>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
}

export interface ServerExportBasis {
  job_id: string;
  run: ExportRunReference;
  calculation_snapshot_digest: string;
  template_bundle: TemplateBundleReference;
  blockers: string[];
  /** Absent on an older server; null and undefined both mean "not supplied". */
  readiness?: ExportReadiness | null;
  /** Latest approval for the CURRENT content binding; absent on an older server. */
  approval?: ReportApproval | null;
}

/**
 * SHA-256 of a buffer, as lowercase hex. WebCrypto's subtle interface only exists in
 * secure contexts, and the demo deployment serves plain http on a public IP - so on
 * that origin the integrity check falls back to this RFC 6234 implementation instead
 * of silently skipping verification.
 */
const SHA256_K = Uint32Array.from([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function sha256HexSync(input: ArrayBuffer): string {
  const data = new Uint8Array(input);
  const bitLength = data.length * 8;
  const padded = new Uint8Array((((data.length + 8) >> 6) + 1) << 6);
  padded.set(data);
  padded[data.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 4, bitLength >>> 0);
  view.setUint32(padded.length - 8, Math.floor(bitLength / 0x100000000));
  const state = Uint32Array.from([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const w = new Uint32Array(64);
  const rotr = (value: number, bits: number) => (value >>> bits) | (value << (32 - bits));
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i += 1) w[i] = view.getUint32(offset + i * 4);
    for (let i = 16; i < 64; i += 1) {
      const w15 = w[i - 15] ?? 0;
      const w2 = w[i - 2] ?? 0;
      const s0 = rotr(w15, 7) ^ rotr(w15, 18) ^ (w15 >>> 3);
      const s1 = rotr(w2, 17) ^ rotr(w2, 19) ^ (w2 >>> 10);
      w[i] = ((w[i - 16] ?? 0) + s0 + (w[i - 7] ?? 0) + s1) >>> 0;
    }
    let a = state[0] ?? 0;
    let b = state[1] ?? 0;
    let c = state[2] ?? 0;
    let d = state[3] ?? 0;
    let e = state[4] ?? 0;
    let f = state[5] ?? 0;
    let g = state[6] ?? 0;
    let hh = state[7] ?? 0;
    for (let i = 0; i < 64; i += 1) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + s1 + ch + (SHA256_K[i] ?? 0) + (w[i] ?? 0)) >>> 0;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (s0 + maj) >>> 0;
      hh = g;
      g = f;
      f = e;
      e = (d + t1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) >>> 0;
    }
    state[0] = ((state[0] ?? 0) + a) >>> 0;
    state[1] = ((state[1] ?? 0) + b) >>> 0;
    state[2] = ((state[2] ?? 0) + c) >>> 0;
    state[3] = ((state[3] ?? 0) + d) >>> 0;
    state[4] = ((state[4] ?? 0) + e) >>> 0;
    state[5] = ((state[5] ?? 0) + f) >>> 0;
    state[6] = ((state[6] ?? 0) + g) >>> 0;
    state[7] = ((state[7] ?? 0) + hh) >>> 0;
  }
  return [...state].map((value) => value.toString(16).padStart(8, "0")).join("");
}

export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle) {
    const digest = await subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map((v) => v.toString(16).padStart(2, "0")).join("");
  }
  return sha256HexSync(bytes);
}

/** The seam the panel depends on, so tests can supply a scripted service. */
export interface ExportsApi {
  readBasis(jobId: string): Promise<ServerExportBasis>;
  createExport(jobId: string, command: CreateExportCommand): Promise<ExportOperation>;
  readExport(jobId: string, exportId: string): Promise<ExportOperation>;
  downloadArtifact(jobId: string, artifact: ExportArtifact): Promise<ExportDownload>;
  submitApproval(jobId: string, command: SubmitApprovalCommand): Promise<ReportApproval>;
  readApproval(jobId: string, approvalId: string): Promise<ReportApproval>;
  decideApproval(
    jobId: string,
    approvalId: string,
    command: ApprovalDecisionCommand,
  ): Promise<ReportApproval>;
}

const DEFAULT_TIMEOUT_MS = 15_000;

export class ExportsClient implements ExportsApi {
  private readonly options: ExportsClientOptions;

  constructor(options: ExportsClientOptions) {
    this.options = options;
  }

  /**
   * 202 acceptance. Replaying the same idempotency key with the same payload returns the
   * original operation; a different payload under the same key is a 409, which the caller
   * resolves by minting a new key, never by mutating the existing operation.
   */
  /**
   * The server-held ingredients a valid request must pin. 409 means no calculation
   * snapshot is registered for the job's current revision yet - the caller shows that
   * state instead of inventing a digest or bundle.
   */
  async readBasis(jobId: string): Promise<ServerExportBasis> {
    const body = (await this.json(
      "GET",
      `/v1/review-jobs/${encode(jobId)}/exports/basis`,
    )) as ServerExportBasis;
    return {
      job_id: body.job_id,
      run: body.run,
      calculation_snapshot_digest: body.calculation_snapshot_digest,
      template_bundle: body.template_bundle,
      blockers: Array.isArray(body.blockers) ? body.blockers : [],
      // Both fields are new; an older server simply does not send them.
      readiness: body.readiness == null ? null : parseExportReadiness(body.readiness),
      approval: body.approval == null ? null : parseReportApproval(body.approval),
    };
  }

  /**
   * 202/200 acceptance. Replaying the same key and payload returns the original approval;
   * a 409 is the service's own refusal (not ready, stale content, or a key conflict).
   */
  async submitApproval(jobId: string, command: SubmitApprovalCommand): Promise<ReportApproval> {
    return parseReportApproval(
      await this.json("POST", `/v1/review-jobs/${encode(jobId)}/report-approvals`, command),
    );
  }

  async readApproval(jobId: string, approvalId: string): Promise<ReportApproval> {
    return parseReportApproval(
      await this.json(
        "GET",
        `/v1/review-jobs/${encode(jobId)}/report-approvals/${encode(approvalId)}`,
      ),
    );
  }

  /** 403 means no approval permission; 409 means wrong state or a key conflict. */
  async decideApproval(
    jobId: string,
    approvalId: string,
    command: ApprovalDecisionCommand,
  ): Promise<ReportApproval> {
    return parseReportApproval(
      await this.json(
        "POST",
        `/v1/review-jobs/${encode(jobId)}/report-approvals/${encode(approvalId)}/decisions`,
        command,
      ),
    );
  }

  async createExport(jobId: string, command: CreateExportCommand): Promise<ExportOperation> {
    return parseExportOperation(
      await this.json("POST", `/v1/review-jobs/${encode(jobId)}/exports`, command),
    );
  }

  async readExport(jobId: string, exportId: string): Promise<ExportOperation> {
    return parseExportOperation(
      await this.json("GET", `/v1/review-jobs/${encode(jobId)}/exports/${encode(exportId)}`),
    );
  }

  /**
   * Binary download only. A non-ok answer or a JSON/HTML body is surfaced as a problem and
   * never returned as file bytes, so an error envelope can never be saved to disk.
   */
  async downloadArtifact(jobId: string, artifact: ExportArtifact): Promise<ExportDownload> {
    return this.transport(async (signal) => {
      const token = await this.options.token();
      const response = await (this.options.fetch ?? globalThis.fetch)(
        `${this.options.baseUrl}/v1/review-jobs/${encode(jobId)}/artifacts/${encode(
          artifact.artifact_id,
        )}/content`,
        {
          signal,
          cache: "no-store",
          credentials: "omit",
          headers: {
            Accept: artifact.content_type,
            ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
          },
        },
      );
      if (!response.ok) {
        const text = await response.text();
        let problem: unknown = null;
        try {
          problem = JSON.parse(text) as unknown;
        } catch {
          /* Use the unknown-outcome fallback below. */
        }
        throw (
          canonicalExportProblem(response.status, problem) ?? new TransportError(UNKNOWN_OUTCOME)
        );
      }
      const contentType = (response.headers.get("Content-Type") ?? "").split(";")[0]?.trim() ?? "";
      const expectedType = artifact.content_type.split(";")[0]?.trim() ?? "";
      if (
        !contentType ||
        contentType.toLowerCase() === "application/json" ||
        contentType.toLowerCase() === "text/html" ||
        (expectedType !== "" && contentType.toLowerCase() !== expectedType.toLowerCase())
      )
        throw new TransportError("The download did not return the expected file type.");
      const bytes = await response.arrayBuffer();
      if (artifact.content_hash) {
        const hash = await sha256Hex(bytes);
        if (hash !== artifact.content_hash)
          throw new TransportError("The downloaded file failed content verification.");
      }
      const filename =
        filenameFromDisposition(response.headers.get("Content-Disposition")) ?? artifact.filename;
      return { bytes, filename, contentType };
    });
  }

  private async json(method: string, path: string, body?: unknown): Promise<unknown> {
    return this.transport(async (signal) => {
      const token = await this.options.token();
      const response = await (this.options.fetch ?? globalThis.fetch)(
        `${this.options.baseUrl}${path}`,
        {
          method,
          signal,
          cache: "no-store",
          credentials: "omit",
          headers: {
            Accept: "application/json",
            ...(body === undefined ? {} : { "Content-Type": "application/json" }),
            ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
          },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        },
      );
      const text = await response.text();
      let payload: unknown = null;
      try {
        payload = JSON.parse(text) as unknown;
      } catch {
        if (response.ok) throw new TransportError(MALFORMED);
      }
      if (!response.ok)
        throw (
          canonicalExportProblem(response.status, payload) ?? new TransportError(UNKNOWN_OUTCOME)
        );
      return payload;
    });
  }

  private async transport<T>(read: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(),
      this.options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    );
    try {
      return await read(controller.signal);
    } catch (cause) {
      if (cause instanceof ExportServiceError || cause instanceof TransportError) throw cause;
      throw new TransportError(
        controller.signal.aborted || (cause instanceof Error && cause.name === "AbortError")
          ? "The service did not answer in time."
          : UNKNOWN_OUTCOME,
      );
    } finally {
      clearTimeout(timer);
    }
  }
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const extended = /filename\*\s*=\s*(?:utf-8''|UTF-8'')?([^;]+)/i.exec(header);
  if (extended?.[1]) {
    try {
      const decoded = decodeURIComponent(extended[1].trim().replace(/^"(.*)"$/, "$1"));
      if (decoded) return decoded;
    } catch {
      /* Fall through to the plain parameter. */
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
  const value = plain?.[1]?.trim();
  return value ? value : null;
}

function encode(segment: string): string {
  return encodeURIComponent(segment);
}
