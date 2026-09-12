import vocabulary from "./diagnostic-contract.json" with { type: "json" };

export interface PrivacyDiagnostic {
  httpStatus: number | null;
  requestId: string | null;
  serverFailure: { stage: string; code: string } | null;
  transportFailure: "deadline_exceeded" | "request_failed" | "response_unusable" | null;
}
const requestIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function responseDiagnostic(
  status: unknown,
  headers: Pick<Headers, "get"> | undefined,
): PrivacyDiagnostic {
  const httpStatus =
    typeof status === "number" && Number.isInteger(status) && status >= 100 && status <= 599
      ? status
      : null;
  const correlation = headers?.get("X-Privacy-Request-Id");
  const requestId = correlation && requestIdPattern.test(correlation) ? correlation : null;
  const stage = headers?.get("X-Privacy-Failure-Stage");
  const code = headers?.get("X-Privacy-Failure-Code");
  const serverFailure =
    httpStatus !== null &&
    httpStatus >= 400 &&
    requestId &&
    stage &&
    vocabulary.stages.includes(stage) &&
    code &&
    vocabulary.codes.includes(code)
      ? { stage, code }
      : null;
  return { httpStatus, requestId, serverFailure, transportFailure: null };
}

export function transportDiagnostic(
  observed: PrivacyDiagnostic | undefined,
  failure: NonNullable<PrivacyDiagnostic["transportFailure"]>,
): PrivacyDiagnostic {
  return { ...(observed ?? responseDiagnostic(undefined, undefined)), transportFailure: failure };
}
