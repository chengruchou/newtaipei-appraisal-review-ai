import type { PrivacyDiagnostic } from "./diagnostics";

export function LocalFailureDetails({
  diagnostic,
}: {
  diagnostic: PrivacyDiagnostic | null | undefined;
}) {
  if (!diagnostic) return null;
  return (
    <aside aria-label="Local privacy failure details">
      <p>HTTP status: {diagnostic.httpStatus ?? "No response observed"}</p>
      {diagnostic.serverFailure ? (
        <>
          <p>Local stage: {diagnostic.serverFailure.stage}</p>
          <p>Failure code: {diagnostic.serverFailure.code}</p>
        </>
      ) : (
        <p>No recognized server failure classification was provided.</p>
      )}
      {diagnostic.requestId ? <p>Local diagnostic reference: {diagnostic.requestId}</p> : null}
      {diagnostic.transportFailure ? <p>Local transport: {diagnostic.transportFailure}</p> : null}
    </aside>
  );
}
