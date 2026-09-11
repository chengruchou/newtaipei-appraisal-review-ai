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

/** No trustworthy outcome was received; the service may already have committed. */
export class TransportError extends Error {
  readonly retryable = true;

  constructor(message: string) {
    super(message);
    this.name = "TransportError";
  }
}

const FALLBACK: Record<number, ServiceErrorCode> = {
  403: "unauthorized",
  404: "not_found",
  409: "version_conflict",
  422: "invalid_request",
  500: "execution_failed",
  503: "capability_unavailable",
};

export function problemFromResponse(
  status: number,
  body: unknown,
  requireCanonical = false,
): ServiceError | TransportError {
  // A proxy status or an arbitrary code-shaped body cannot prove a write was rejected.
  // Read requests retain their status fallback because they cannot commit a command.
  if (
    requireCanonical &&
    (!validateResponse("ServiceProblem", body) ||
      (body as ServiceProblem).code !== FALLBACK[status])
  ) {
    return new TransportError("The service did not return a validated submission outcome.");
  }
  const code = (body as ServiceProblem | null)?.code;
  const known: ServiceErrorCode | undefined =
    code !== undefined && Object.hasOwn(EXPLANATIONS, code) ? code : FALLBACK[status];
  return new ServiceError(known ?? "execution_failed", status);
}

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
