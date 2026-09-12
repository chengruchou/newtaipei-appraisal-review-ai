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

export interface ExportsClientOptions {
  baseUrl: string;
  token: () => Promise<string | null>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
}

/** The seam the panel depends on, so tests can supply a scripted service. */
export interface ExportsApi {
  createExport(jobId: string, command: CreateExportCommand): Promise<ExportOperation>;
  readExport(jobId: string, exportId: string): Promise<ExportOperation>;
  downloadArtifact(jobId: string, artifact: ExportArtifact): Promise<ExportDownload>;
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
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const hash = [...new Uint8Array(digest)]
          .map((v) => v.toString(16).padStart(2, "0"))
          .join("");
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
