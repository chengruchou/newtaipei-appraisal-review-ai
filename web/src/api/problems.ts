import type { components } from "./schema";
import { validateResponse } from "./validation";

export type ServiceProblem = components["schemas"]["ServiceProblem"];
export type ServiceErrorCode = ServiceProblem["code"];

/**
 * The service answers with six machine codes and one fixed message. The message is
 * deliberately generic, so the workbench supplies its own wording rather than showing a
 * sentence that says nothing to a reviewer.
 */
export class ServiceError extends Error {
  readonly code: ServiceErrorCode;
  readonly status: number;

  constructor(code: ServiceErrorCode, status: number) {
    super(code);
    this.name = "ServiceError";
    this.code = code;
    this.status = status;
  }
}

/** A request that never reached the service, so the caller cannot know if it applied. */
export class TransportError extends Error {
  readonly retryable = true;

  constructor(message: string) {
    super(message);
    this.name = "TransportError";
  }
}

/**
 * A refusal is definitive only when the service itself said so, in its own canonical
 * envelope. Everything else leaves the outcome genuinely unknown, and `null` says so.
 *
 * The distinction is not pedantry. An intermediary can fail *after* the service already
 * committed the write: a gateway 502 or 504 carrying HTML says the reviewer's answer may
 * well have been recorded. Reading authority off the HTTP status alone would let that be
 * shown as a decision the service never made, and would throw away the command and
 * idempotency key that are the only way to recover the receipt.
 *
 * So the body must validate against the published `ServiceProblem` schema, and its code
 * must be one this version knows. An unrecognised code from a future version is also an
 * unknown outcome, not a refusal we may paraphrase.
 */
export function canonicalProblem(status: number, body: unknown): ServiceError | null {
  if (!validateResponse("ServiceProblem", body)) return null;
  const code = (body as ServiceProblem).code;
  return code in EXPLANATIONS ? new ServiceError(code, status) : null;
}

/** Shown when an intermediary answered and the write may or may not have been applied. */
export const UNKNOWN_OUTCOME =
  "The service did not give a usable answer, so it is unclear whether this was recorded.";

/**
 * Reviewer-facing wording. Each says what happened and what to do; none invites a blind
 * retry of a write, because a conflict means the reviewer must look at newer state first.
 */
export const EXPLANATIONS: Record<ServiceErrorCode, { title: string; guidance: string }> = {
  invalid_request: {
    title: "This response could not be accepted",
    guidance:
      "The service rejected the submission as malformed. Reload the task and enter the response again.",
  },
  unauthorized: {
    title: "You do not have permission for this action",
    guidance:
      "Your account can view this task but not answer it. Ask a reviewer with the required permission to respond.",
  },
  not_found: {
    title: "This task is not available",
    guidance:
      "It may belong to another reviewer, or it may never have existed. Return to the job list.",
  },
  version_conflict: {
    title: "Someone else changed this first",
    guidance:
      "The task or the case revision moved on before your response was committed. Nothing was saved. Reload to see the current state, then decide again.",
  },
  capability_unavailable: {
    title: "This service is not configured",
    guidance: "The review plane is not available in this deployment. No work has been lost.",
  },
  execution_failed: {
    title: "The service could not complete this",
    guidance: "This is a fault on the service side, not something you did. Try again shortly.",
  },
};

/** A conflict is recoverable by re-reading, never by resending the same write. */
export function isRecoverableByReload(error: unknown): boolean {
  return error instanceof ServiceError && error.code === "version_conflict";
}
