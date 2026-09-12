import { validateResponse } from "./validation";
import { newOperationKey } from "./ids";
import { canonicalProblem, ServiceError, TransportError, UNKNOWN_OUTCOME } from "./problems";
import type { components } from "./schema";

export type ReviewSessionView = components["schemas"]["ReviewSessionView"];
export type CaseContextView = components["schemas"]["CaseContextView"];
export type PausedReviewView = components["schemas"]["PausedReviewView"];

export type HumanTask = components["schemas"]["HumanTask"];
export type TaskSubjectView = components["schemas"]["TaskSubjectView"];
export type TaskView = components["schemas"]["TaskView"];
export type ValueRevision = components["schemas"]["ValueRevision-Input"];
export type PublicValue = components["schemas"]["PublicValue-Input"];
export type HumanResponse = components["schemas"]["HumanResponse"];
export type TaskListView = components["schemas"]["TaskListView"];
export type RevisionListView = components["schemas"]["RevisionListView"];
export type ResponseReceipt = components["schemas"]["ResponseReceipt"];
export type JobStatusView = components["schemas"]["JobStatusView"];
export type ServiceResult = components["schemas"]["ServiceResult"];
export type MaterialRevision = components["schemas"]["MaterialRevision"];
export type ArtifactManifest = ServiceResult["artifacts"][number];
export type SourceCitation = components["schemas"]["SourceCitation"];

export interface ClientOptions {
  baseUrl: string;
  /**
   * Supplies the bearer token per request. The workbench never stores a token in
   * localStorage and never decodes one to decide what to show: #25 forbids the frontend
   * inferring its own permissions, so authority comes only from what the service answers.
   */
  token: () => Promise<string | null>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
  onUnauthorized?: () => void;
  localOriginalPreview?: { baseUrl: string; pairingToken: string };
}

const DEFAULT_TIMEOUT_MS = 15_000;

export class ReviewClient {
  private readonly options: ClientOptions;
  private readonly active = new Set<AbortController>();
  private disposed = false;
  private sourceMode: ReviewSessionView["data_mode"] = "unspecified";

  constructor(options: ClientOptions) {
    if (options.localOriginalPreview) {
      const base = new URL(options.localOriginalPreview.baseUrl);
      if (
        base.protocol !== "http:" ||
        base.hostname !== "127.0.0.1" ||
        !base.port ||
        base.username ||
        base.password ||
        base.pathname !== "/" ||
        base.search ||
        base.hash
      )
        throw new Error("Original preview requires a configured numeric loopback origin");
    }
    this.options = options.localOriginalPreview
      ? {
          ...options,
          localOriginalPreview: {
            ...options.localOriginalPreview,
            baseUrl: new URL(options.localOriginalPreview.baseUrl).origin,
          },
        }
      : options;
  }

  dispose(): void {
    this.disposed = true;
    this.active.forEach((controller) => controller.abort());
    this.active.clear();
  }

  async readSession(): Promise<ReviewSessionView> {
    const session = await this.request<ReviewSessionView>(
      "ReviewSessionView",
      "GET",
      "/v1/review-session",
    );
    this.sourceMode = session.data_mode;
    return session;
  }

  async readCaseContext(jobId: string): Promise<CaseContextView> {
    return this.request<CaseContextView>(
      "CaseContextView",
      "GET",
      `/v1/review-jobs/${encode(jobId)}/context`,
    );
  }

  async readAssessment(jobId: string): Promise<PausedReviewView> {
    return this.request<PausedReviewView>(
      "PausedReviewView",
      "GET",
      `/v1/review-jobs/${encode(jobId)}/assessment`,
    );
  }

  async readResponse(taskId: string, key: string): Promise<ResponseReceipt> {
    return this.request<ResponseReceipt>(
      "ResponseReceipt",
      "GET",
      `/v1/review-tasks/${encode(taskId)}/responses/${encode(key)}`,
    );
  }

  async listJobTasks(jobId: string): Promise<TaskListView> {
    return this.request<TaskListView>(
      "TaskListView",
      "GET",
      `/v1/review-jobs/${encode(jobId)}/tasks`,
    );
  }

  async listJobRevisions(jobId: string): Promise<RevisionListView> {
    return this.request<RevisionListView>(
      "RevisionListView",
      "GET",
      `/v1/review-jobs/${encode(jobId)}/revisions`,
    );
  }

  async readJob(jobId: string): Promise<JobStatusView> {
    return this.request<JobStatusView>("JobStatusView", "GET", `/v1/review-jobs/${encode(jobId)}`);
  }

  async readJobResult(jobId: string): Promise<ServiceResult> {
    return this.request<ServiceResult>(
      "ServiceResult",
      "GET",
      `/v1/review-jobs/${encode(jobId)}/result`,
    );
  }

  async readTask(taskId: string): Promise<TaskView> {
    return this.request<TaskView>("TaskView", "GET", `/v1/review-tasks/${encode(taskId)}`);
  }

  async readTaskSubject(taskId: string): Promise<TaskSubjectView> {
    return this.request<TaskSubjectView>(
      "TaskSubjectView",
      "GET",
      `/v1/review-tasks/${encode(taskId)}/subject`,
    );
  }

  /**
   * Submitting is safe to repeat only because the body carries an idempotency key the
   * caller chose once. A retry with the same key returns the first receipt unchanged; a
   * different payload under that key is refused. Never mint a fresh key on retry.
   */
  async submitResponse(taskId: string, command: HumanResponse): Promise<ResponseReceipt> {
    return this.request<ResponseReceipt>(
      "ResponseReceipt",
      "POST",
      `/v1/review-tasks/${encode(taskId)}/responses`,
      command,
    );
  }

  async readSource(citation: SourceCitation): Promise<ArrayBuffer> {
    const query = new URLSearchParams({
      version: citation.version,
      content_hash: citation.content_hash,
    });
    if (this.sourceMode === "local_original") {
      const preview = this.options.localOriginalPreview;
      if (!preview?.pairingToken) throw new ServiceError("capability_unavailable", 503);
      return this.readPdf(
        `/local-original/documents/${encode(citation.document_id)}?${query}`,
        citation.content_hash,
        preview,
      );
    }
    return this.readPdf(
      `/v1/documents/${encode(citation.document_id)}/content?${query}`,
      citation.content_hash,
    );
  }

  async downloadArtifact(jobId: string, artifact: ArtifactManifest): Promise<ArrayBuffer> {
    return this.readPdf(
      `/v1/review-jobs/${encode(jobId)}/artifacts/${encode(artifact.artifact_id)}/content`,
      artifact.content_hash,
    );
  }

  /* --------------------------------------------------------------------- *
   * Email-login session and case-intake plane.
   *
   * These service routes are deployed ahead of the regenerated OpenAPI spec, so —
   * exactly like src/api/exports.ts — this section carries its own structural checks
   * instead of `validateResponse`, with the same rule: only the service's own answer
   * is a decision, everything else is an unknown outcome.
   * --------------------------------------------------------------------- */

  /** GET /v1/session — who this bearer token is and when it expires. */
  async readAuthSession(): Promise<AuthSessionView> {
    return parseAuthSession(await this.intakeJson("GET", "/v1/session"));
  }

  /**
   * DELETE /v1/session — revoke the current token on the service.
   *
   * Deliberately NOT routed through `transport`: logout tears the client down right
   * after calling this, and disposing must not abort the revocation request itself.
   * A 401/403/404 answer means the token is already unusable, which is the outcome
   * logout wanted, so those resolve rather than reject.
   */
  async revokeSession(): Promise<void> {
    const token = await this.options.token();
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(),
      this.options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    );
    try {
      const response = await (this.options.fetch ?? globalThis.fetch)(
        `${this.options.baseUrl}/v1/session`,
        {
          method: "DELETE",
          signal: controller.signal,
          cache: "no-store",
          credentials: "omit",
          headers: {
            Accept: "application/json",
            ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
          },
        },
      );
      if (!response.ok && ![401, 403, 404].includes(response.status))
        throw new IntakeRequestError(response.status, null);
    } finally {
      clearTimeout(timer);
    }
  }

  /** POST /v1/cases — 201 CaseRecord. Retries must reuse the same idempotency key. */
  async createCase(command: CreateCaseCommand): Promise<CaseRecord> {
    return parseCaseRecord(await this.intakeJson("POST", "/v1/cases", { json: command }));
  }

  /** GET /v1/cases/{id}/materials — what the service actually holds for this case. */
  async listCaseMaterials(caseId: string): Promise<MaterialListView> {
    const payload = await this.intakeJson("GET", `/v1/cases/${encode(caseId)}/materials`);
    if (!isRecord(payload)) throw new TransportError(INTAKE_MALFORMED);
    const materials = Array.isArray(payload.materials)
      ? payload.materials.map(parseMaterialRecord)
      : [];
    return { materials };
  }

  /**
   * POST /v1/cases/{id}/materials — raw request body plus identity headers.
   *
   * The service enforces the 64MB cap and answers with the stored sha256; the caller
   * verifies that digest against the bytes it read, and only then claims "已核對".
   * Uploads get a longer deadline than JSON calls because the body is the file itself.
   */
  async uploadCaseMaterial(
    caseId: string,
    upload: { bytes: ArrayBuffer; filename: string; contentType: string; idempotencyKey: string },
  ): Promise<MaterialRecord> {
    return parseMaterialRecord(
      await this.intakeJson("POST", `/v1/cases/${encode(caseId)}/materials`, {
        raw: upload.bytes,
        headers: {
          "Content-Type": upload.contentType || "application/octet-stream",
          "X-Upload-Filename": headerSafeFilename(upload.filename),
          "X-Idempotency-Key": upload.idempotencyKey,
        },
        timeoutMs: UPLOAD_TIMEOUT_MS,
      }),
    );
  }

  /** Shared fetch/parse/problem boundary for the intake plane (no generated validator). */
  private async intakeJson(
    method: string,
    path: string,
    init?: {
      json?: unknown;
      raw?: ArrayBuffer;
      headers?: Record<string, string>;
      timeoutMs?: number;
    },
  ): Promise<unknown> {
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
            ...(init?.json === undefined ? {} : { "Content-Type": "application/json" }),
            ...(init?.headers ?? {}),
            ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
          },
          ...(init?.json !== undefined
            ? { body: JSON.stringify(init.json) }
            : init?.raw !== undefined
              ? { body: init.raw }
              : {}),
        },
      );
      const text = await response.text();
      let payload: unknown = null;
      try {
        payload = JSON.parse(text) as unknown;
      } catch {
        if (response.ok && response.status !== 204 && text.trim() !== "")
          throw new TransportError(INTAKE_MALFORMED);
      }
      if (!response.ok) throw intakeProblem(response.status, payload);
      return payload;
    }, init?.timeoutMs);
  }

  private async readPdf(
    path: string,
    expectedHash: string,
    preview?: ClientOptions["localOriginalPreview"],
  ): Promise<ArrayBuffer> {
    return this.transport(async (signal) => {
      const token = await this.options.token();
      const response = await (this.options.fetch ?? globalThis.fetch)(
        `${preview?.baseUrl ?? this.options.baseUrl}${path}`,
        {
          signal,
          cache: "no-store",
          credentials: "omit",
          redirect: "error",
          headers: {
            Accept: "application/pdf",
            ...(preview
              ? {
                  Authorization: `Bearer ${preview.pairingToken}`,
                  "X-Review-Session": token === null ? "" : `Bearer ${token}`,
                }
              : token === null
                ? {}
                : { Authorization: `Bearer ${token}` }),
          },
        },
      );
      if (!response.ok) {
        const text = await response.text();
        let problem: unknown = null;
        try {
          problem = JSON.parse(text) as unknown;
        } catch {
          /* Use the HTTP problem fallback. */
        }
        throw canonicalProblem(response.status, problem) ?? new TransportError(UNKNOWN_OUTCOME);
      }
      if (response.headers.get("Content-Type")?.split(";")[0] !== "application/pdf")
        throw new Error("Unexpected content type");
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength > 32 * 1024 * 1024) throw new Error("PDF exceeds client limit");
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const hash = [...new Uint8Array(digest)].map((v) => v.toString(16).padStart(2, "0")).join("");
      if (hash !== expectedHash) throw new Error("PDF identity mismatch");
      return bytes;
    });
  }

  private async request<T>(
    schema: string,
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const doFetch = this.options.fetch ?? globalThis.fetch;
    return this.transport(async (signal) => {
      const token = await this.options.token();
      const response = await doFetch(`${this.options.baseUrl}${path}`, {
        method,
        signal,
        headers: {
          Accept: "application/json",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const text = await response.text();
      let payload: unknown;
      try {
        payload = JSON.parse(text) as unknown;
      } catch (error) {
        if (response.ok) throw error;
        payload = null;
      }
      if (!response.ok) {
        // Only the service's own canonical envelope is a decision. A gateway's HTML is not,
        // and the caller must keep its command and key to recover from it.
        throw canonicalProblem(response.status, payload) ?? new TransportError(UNKNOWN_OUTCOME);
      }
      if (!validateResponse(schema, payload)) throw new Error("Invalid service response");
      return payload as T;
    });
  }

  /** Headers, body, parsing and validation share one deadline and unknown-outcome boundary. */
  private async transport<T>(
    read: (signal: AbortSignal) => Promise<T>,
    timeoutMs?: number,
  ): Promise<T> {
    if (this.disposed) throw new TransportError("This session has ended.");
    const controller = new AbortController();
    this.active.add(controller);
    const duration = timeoutMs ?? this.options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const deadline = Date.now() + duration;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<never>((_resolve, reject) => {
      timeout = setTimeout(() => {
        controller.abort();
        reject(new DOMException("Deadline exceeded", "AbortError"));
      }, duration);
    });
    try {
      const value = await Promise.race([read(controller.signal), expired]);
      // Parsing and synchronous validation can occupy the event loop past the deadline.
      if (this.disposed || Date.now() >= deadline)
        throw new DOMException("Deadline exceeded", "AbortError");
      return value;
    } catch (cause) {
      if (cause instanceof ServiceError) {
        if (cause.code === "unauthorized" && !this.disposed) this.options.onUnauthorized?.();
        throw cause;
      }
      // An intake refusal carries its own HTTP status; the caller words it for the user.
      if (cause instanceof IntakeRequestError) throw cause;
      // An unknown outcome already described precisely keeps its own wording.
      if (cause instanceof TransportError) throw cause;
      throw new TransportError(
        controller.signal.aborted || (cause instanceof Error && cause.name === "AbortError")
          ? "The service did not answer in time."
          : "The service response could not be read or validated.",
      );
    } finally {
      clearTimeout(timeout);
      this.active.delete(controller);
    }
  }
}

function encode(segment: string): string {
  return encodeURIComponent(segment);
}

/**
 * One key per reviewed command. Every retry carries that same command and key.
 *
 * Minting goes through `newOperationKey`, which works on insecure origins where
 * `crypto.randomUUID` does not exist and throws `OperationKeySourceError` when no
 * cryptographic randomness is available at all. Callers mint inside their submit
 * handler's try/catch so that failure surfaces as an explicit message.
 */
export function newIdempotencyKey(random?: () => string): string {
  return random ? `wb-${random()}` : newOperationKey("wb");
}

/* --------------------------------------------------------------------------- *
 * Email-login and case-intake contract (deployed ahead of the OpenAPI spec).
 *
 * Types are exported so other panels can reuse them without editing this file.
 * --------------------------------------------------------------------------- */

/** 200 answer of POST /v1/auth/verify. The token is a normal bearer session. */
export interface LoginGrant {
  token: string;
  expires_at: string | number | null;
  actor_id: string;
}

/** GET /v1/session — the service's own description of the current bearer session. */
export interface AuthSessionView {
  actor_id: string;
  kind: string | null;
  expires_at: string | number | null;
  permissions_summary: string | null;
}

export interface CreateCaseCommand {
  schema_version: "service-v1";
  idempotency_key: string;
  title: string;
  district: string;
  valuation_date?: string;
}

/** 201 answer of POST /v1/cases. */
export interface CaseRecord {
  case_id: string;
  title: string;
  district: string;
  created_at: string | number | null;
  valuation_date: string | null;
}

/** One stored material; sha256 is the service's stored digest, not a client claim. */
export interface MaterialRecord {
  material_id: string;
  sha256: string;
  size: number | null;
  filename: string | null;
  content_type: string | null;
  created_at: string | number | null;
}

export interface MaterialListView {
  materials: MaterialRecord[];
}

/** Service-side cap on one material body; the UI refuses larger files before sending. */
export const MATERIAL_SIZE_LIMIT_BYTES = 64 * 1024 * 1024;

const UPLOAD_TIMEOUT_MS = 120_000;
const INTAKE_MALFORMED = "The service response could not be read or validated.";

/**
 * A definitive intake refusal that is not one of the six canonical review-plane codes
 * (for example 413 payload-too-large or 429 rate-limited). Carries the HTTP status so
 * the caller can word it; it never carries the request body back.
 */
export class IntakeRequestError extends Error {
  readonly status: number;
  readonly serviceMessage: string | null;

  constructor(status: number, serviceMessage: string | null = null) {
    super(`Intake request refused with HTTP ${status}`);
    this.name = "IntakeRequestError";
    this.status = status;
    this.serviceMessage = serviceMessage;
  }
}

/** How a login-plane request was refused. "code_rejected" is deliberately generic. */
export type LoginRefusalKind = "code_rejected" | "rate_limited" | "unavailable";

export class LoginRefusedError extends Error {
  readonly kind: LoginRefusalKind;
  readonly status: number;

  constructor(kind: LoginRefusalKind, status: number) {
    super(`Login request refused (${kind})`);
    this.name = "LoginRefusedError";
    this.kind = kind;
    this.status = status;
  }
}

export interface AuthPlaneOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
}

/**
 * POST /v1/auth/request-code. The service always answers 202 whether or not the
 * mailbox exists, and the caller must show one neutral sentence either way — never
 * an account-existence oracle. Only a rate limit or an outage is surfaced.
 */
export async function requestLoginCode(options: AuthPlaneOptions, email: string): Promise<void> {
  const response = await authPlaneFetch(options, "/v1/auth/request-code", { email });
  if (response.ok) return;
  throw new LoginRefusedError(
    response.status === 429 ? "rate_limited" : "unavailable",
    response.status,
  );
}

/**
 * POST /v1/auth/verify — 200 {token, expires_at, actor_id} or a deliberately generic
 * 403. The code is never echoed back and never logged; a refusal carries no detail
 * beyond its kind.
 */
export async function verifyLoginCode(
  options: AuthPlaneOptions,
  email: string,
  code: string,
): Promise<LoginGrant> {
  const response = await authPlaneFetch(options, "/v1/auth/verify", { email, code });
  if (!response.ok) {
    if (response.status === 429) throw new LoginRefusedError("rate_limited", 429);
    if ([400, 401, 403, 404, 422].includes(response.status))
      throw new LoginRefusedError("code_rejected", response.status);
    throw new LoginRefusedError("unavailable", response.status);
  }
  let payload: unknown = null;
  try {
    payload = JSON.parse(await response.text()) as unknown;
  } catch {
    throw new TransportError(INTAKE_MALFORMED);
  }
  if (
    !isRecord(payload) ||
    typeof payload.token !== "string" ||
    payload.token.trim() === "" ||
    typeof payload.actor_id !== "string"
  )
    throw new TransportError(INTAKE_MALFORMED);
  return {
    token: payload.token,
    actor_id: payload.actor_id,
    expires_at: timestampOrNull(payload.expires_at),
  };
}

async function authPlaneFetch(
  options: AuthPlaneOptions,
  path: string,
  body: Record<string, string>,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  try {
    return await (options.fetch ?? globalThis.fetch)(`${options.baseUrl}${path}`, {
      method: "POST",
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    throw new TransportError(
      controller.signal.aborted || (cause instanceof Error && cause.name === "AbortError")
        ? "The service did not answer in time."
        : "The login service could not be reached.",
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Canonical envelope first; otherwise the HTTP status is still an authorization fact. */
function intakeProblem(status: number, body: unknown): Error {
  const canonical = canonicalProblem(status, body);
  if (canonical) return canonical;
  // The intake plane predates the canonical envelope on some answers: a bare 401/403
  // still means this bearer session is not accepted, and must behave like one.
  if (status === 401 || status === 403) return new ServiceError("unauthorized", status);
  const message =
    isRecord(body) && typeof body.message === "string" && body.message.trim()
      ? body.message.trim()
      : null;
  return new IntakeRequestError(status, message);
}

function parseAuthSession(payload: unknown): AuthSessionView {
  if (!isRecord(payload) || typeof payload.actor_id !== "string")
    throw new TransportError(INTAKE_MALFORMED);
  return {
    actor_id: payload.actor_id,
    kind: typeof payload.kind === "string" ? payload.kind : null,
    expires_at: timestampOrNull(payload.expires_at),
    permissions_summary:
      typeof payload.permissions_summary === "string" ? payload.permissions_summary : null,
  };
}

function parseCaseRecord(payload: unknown): CaseRecord {
  if (!isRecord(payload) || typeof payload.case_id !== "string" || payload.case_id === "")
    throw new TransportError(INTAKE_MALFORMED);
  return {
    case_id: payload.case_id,
    title: typeof payload.title === "string" ? payload.title : "",
    district: typeof payload.district === "string" ? payload.district : "",
    created_at: timestampOrNull(payload.created_at),
    valuation_date: typeof payload.valuation_date === "string" ? payload.valuation_date : null,
  };
}

function parseMaterialRecord(payload: unknown): MaterialRecord {
  if (
    !isRecord(payload) ||
    typeof payload.material_id !== "string" ||
    typeof payload.sha256 !== "string"
  )
    throw new TransportError(INTAKE_MALFORMED);
  return {
    material_id: payload.material_id,
    sha256: payload.sha256,
    size: typeof payload.size === "number" ? payload.size : null,
    filename: typeof payload.filename === "string" ? payload.filename : null,
    content_type: typeof payload.content_type === "string" ? payload.content_type : null,
    created_at: timestampOrNull(payload.created_at),
  };
}

function timestampOrNull(value: unknown): string | number | null {
  return typeof value === "string" || typeof value === "number" ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * HTTP header values must stay within ISO-8859-1; a Chinese filename would make
 * `fetch` itself throw. Non-ASCII names are sent percent-encoded — the service
 * stores what it receives, so the list may show the encoded form for such names.
 */
function headerSafeFilename(filename: string): string {
  const trimmed = filename.trim() === "" ? "unnamed-upload" : filename.trim();
  return /^[\x20-\x7e]+$/.test(trimmed) ? trimmed : encodeURIComponent(trimmed);
}
