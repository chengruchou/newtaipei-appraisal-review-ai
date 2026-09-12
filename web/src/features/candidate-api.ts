import { TransportError, UNKNOWN_OUTCOME, type ServiceErrorCode } from "@/api/problems";
import { readToken } from "@/config";

/**
 * Hand-written client for the fact-candidate routes
 * (`/v1/cases/{case_id}/candidates`). Same rules as the other panel clients: only the
 * service's canonical problem envelope is a decision, everything else is an unknown
 * outcome, and the panel never invents data the service did not answer.
 *
 * The confirm body mirrors src/appraisal_review/api/routes/fact_candidates.py exactly:
 * the accepted fields must echo the stored candidate verbatim (value, unit, applicable
 * date and evidence sha256), and the service refuses anything else with a 409.
 */

export type CandidateStatus = "candidate" | "confirmed" | "rejected" | "superseded";
export type CandidateDecision = "accept" | "reject";

export interface CandidateEvidence {
  url: string;
  sha256: string;
  /** Server clock, seconds since the epoch. */
  retrieved_at: number;
  excerpt?: string;
  row_locator?: string | null;
}

export interface FactCandidate {
  candidate_id: string;
  case_id: string;
  revision_id: string;
  subject_id: string;
  field_key: string;
  value: string;
  unit: string | null;
  applicable_date: string | null;
  source_id: string;
  evidence: CandidateEvidence;
  status: CandidateStatus;
  created_by: { actor_id: string; kind: string };
  created_at: number;
}

export interface CandidateListView {
  case_id: string;
  candidates: FactCandidate[];
}

export interface ConfirmCandidateCommand {
  schema_version: "service-v1";
  idempotency_key: string;
  decision: CandidateDecision;
  /** Required by the service for a rejection. */
  reason?: string;
  expected_revision: string;
  accepted_value: string;
  accepted_unit: string | null;
  accepted_applicable_date: string | null;
  evidence_sha256: string;
}

export interface CandidateConfirmation {
  receipt_id: string;
  candidate_id: string;
  case_id: string;
  subject_id: string;
  field_key: string;
  decision: CandidateDecision;
  reason: string | null;
  actor: { actor_id: string; kind: string };
  decided_at: number;
}

/** A definitive refusal from the service, in its canonical envelope. */
export class CandidateServiceError extends Error {
  readonly code: ServiceErrorCode;
  readonly status: number;
  readonly serviceMessage: string | null;

  constructor(code: ServiceErrorCode, status: number, serviceMessage: string | null = null) {
    super(code);
    this.name = "CandidateServiceError";
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

/** 401 (no session) and 403 (no permission) both surface as unauthorized. */
const EXPECTED_STATUS: Record<number, ServiceErrorCode> = {
  401: "unauthorized",
  403: "unauthorized",
  404: "not_found",
  409: "version_conflict",
  422: "invalid_request",
  500: "execution_failed",
  503: "capability_unavailable",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function canonicalCandidateProblem(status: number, body: unknown): CandidateServiceError | null {
  const expected = EXPECTED_STATUS[status];
  if (!expected || !isRecord(body) || typeof body.code !== "string") return null;
  if (!(KNOWN_CODES as readonly string[]).includes(body.code) || body.code !== expected)
    return null;
  const message =
    typeof body.message === "string" && body.message.trim() ? body.message.trim() : null;
  return new CandidateServiceError(expected, status, message);
}

const MALFORMED = "The service response could not be read or validated.";

const STATUSES: readonly string[] = ["candidate", "confirmed", "rejected", "superseded"];

function parseEvidence(value: unknown): CandidateEvidence {
  if (
    !isRecord(value) ||
    typeof value.url !== "string" ||
    typeof value.sha256 !== "string" ||
    typeof value.retrieved_at !== "number"
  )
    throw new TransportError(MALFORMED);
  return {
    url: value.url,
    sha256: value.sha256,
    retrieved_at: value.retrieved_at,
    excerpt: typeof value.excerpt === "string" ? value.excerpt : "",
    row_locator: typeof value.row_locator === "string" ? value.row_locator : null,
  };
}

function parseActor(value: unknown): { actor_id: string; kind: string } {
  if (!isRecord(value) || typeof value.actor_id !== "string" || typeof value.kind !== "string")
    throw new TransportError(MALFORMED);
  return { actor_id: value.actor_id, kind: value.kind };
}

export function parseFactCandidate(payload: unknown): FactCandidate {
  if (
    !isRecord(payload) ||
    typeof payload.candidate_id !== "string" ||
    typeof payload.case_id !== "string" ||
    typeof payload.revision_id !== "string" ||
    typeof payload.subject_id !== "string" ||
    typeof payload.field_key !== "string" ||
    typeof payload.value !== "string" ||
    typeof payload.source_id !== "string" ||
    typeof payload.status !== "string" ||
    !STATUSES.includes(payload.status) ||
    typeof payload.created_at !== "number"
  )
    throw new TransportError(MALFORMED);
  return {
    candidate_id: payload.candidate_id,
    case_id: payload.case_id,
    revision_id: payload.revision_id,
    subject_id: payload.subject_id,
    field_key: payload.field_key,
    value: payload.value,
    unit: typeof payload.unit === "string" ? payload.unit : null,
    applicable_date: typeof payload.applicable_date === "string" ? payload.applicable_date : null,
    source_id: payload.source_id,
    evidence: parseEvidence(payload.evidence),
    status: payload.status as CandidateStatus,
    created_by: parseActor(payload.created_by),
    created_at: payload.created_at,
  };
}

export function parseCandidateList(payload: unknown): CandidateListView {
  if (!isRecord(payload) || typeof payload.case_id !== "string")
    throw new TransportError(MALFORMED);
  const rows = Array.isArray(payload.candidates) ? payload.candidates : [];
  return { case_id: payload.case_id, candidates: rows.map(parseFactCandidate) };
}

export function parseConfirmation(payload: unknown): CandidateConfirmation {
  if (
    !isRecord(payload) ||
    typeof payload.receipt_id !== "string" ||
    typeof payload.candidate_id !== "string" ||
    typeof payload.case_id !== "string" ||
    typeof payload.subject_id !== "string" ||
    typeof payload.field_key !== "string" ||
    (payload.decision !== "accept" && payload.decision !== "reject") ||
    typeof payload.decided_at !== "number"
  )
    throw new TransportError(MALFORMED);
  return {
    receipt_id: payload.receipt_id,
    candidate_id: payload.candidate_id,
    case_id: payload.case_id,
    subject_id: payload.subject_id,
    field_key: payload.field_key,
    decision: payload.decision,
    reason: typeof payload.reason === "string" ? payload.reason : null,
    actor: parseActor(payload.actor),
    decided_at: payload.decided_at,
  };
}

/** The seam the panel depends on, so tests can supply a scripted service. */
export interface CandidateApi {
  listCandidates(caseId: string): Promise<CandidateListView>;
  confirmCandidate(
    caseId: string,
    candidateId: string,
    command: ConfirmCandidateCommand,
  ): Promise<CandidateConfirmation>;
}

export interface CandidateClientOptions {
  baseUrl: string;
  token: () => Promise<string | null>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 15_000;

export class CandidateClient implements CandidateApi {
  private readonly options: CandidateClientOptions;

  constructor(options: CandidateClientOptions) {
    this.options = options;
  }

  async listCandidates(caseId: string): Promise<CandidateListView> {
    return parseCandidateList(await this.json("GET", `/v1/cases/${encode(caseId)}/candidates`));
  }

  /**
   * 201 acceptance. Replaying the same key and payload returns the original receipt; a
   * changed echo, a stale expected_revision or a spent candidate is the service's own 409.
   */
  async confirmCandidate(
    caseId: string,
    candidateId: string,
    command: ConfirmCandidateCommand,
  ): Promise<CandidateConfirmation> {
    return parseConfirmation(
      await this.json(
        "POST",
        `/v1/cases/${encode(caseId)}/candidates/${encode(candidateId)}/confirm`,
        command,
      ),
    );
  }

  private async json(method: string, path: string, body?: unknown): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(),
      this.options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    );
    try {
      const token = await this.options.token();
      const response = await (this.options.fetch ?? globalThis.fetch)(
        `${this.options.baseUrl}${path}`,
        {
          method,
          signal: controller.signal,
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
          canonicalCandidateProblem(response.status, payload) ?? new TransportError(UNKNOWN_OUTCOME)
        );
      return payload;
    } catch (cause) {
      if (cause instanceof CandidateServiceError || cause instanceof TransportError) throw cause;
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

function encode(segment: string): string {
  return encodeURIComponent(segment);
}

/** Same origin and per-request token pattern as the other panel clients. */
export function buildCandidateClient(): CandidateClient {
  const baseUrl: string = import.meta.env.VITE_API_BASE_URL ?? "";
  return new CandidateClient({ baseUrl, token: () => Promise.resolve(readToken()) });
}
