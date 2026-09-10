import { useCallback, useState } from "react";
import type { ArtifactManifest, ReviewClient, ServiceResult, SourceCitation } from "@/api/client";
import { EXPLANATIONS, ServiceError } from "@/api/problems";
import { EvidenceList } from "@/ui/Evidence";

export function ResultPanel({ client, jobId }: { client: ReviewClient; jobId: string }) {
  const [result, setResult] = useState<ServiceResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const loadSource = useCallback(
    (citation: SourceCitation) => client.readSource(citation),
    [client],
  );

  async function read() {
    setBusy(true);
    setResult(null);
    setMessage("");
    try {
      setResult(await client.readJobResult(jobId));
    } catch (error) {
      setMessage(
        error instanceof ServiceError
          ? EXPLANATIONS[error.code].guidance
          : "The result could not be read. Try loading it again.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function download(artifact: ArtifactManifest) {
    setBusy(true);
    setMessage("");
    try {
      // Always ask the authenticated endpoint again. A past result is not download authority.
      const bytes = await client.downloadArtifact(jobId, artifact);
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${artifact.artifact_id}.pdf`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage("Verified PDF downloaded. Its hash matches this result's artifact manifest.");
    } catch {
      setMessage(
        "The PDF could not be authorized or verified. Reload the result before trying again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Review result">
      <h2>Result and downloads</h2>
      <button type="button" disabled={busy} onClick={() => void read()}>
        {busy ? "Loading…" : "Load current result"}
      </button>
      {message ? <p role="status">{message}</p> : null}
      {result ? (
        <>
          <p>
            Execution: {result.execution_status}. Review:{" "}
            {result.business_status ?? "not available"}. PDF: {result.artifact_status}.
          </p>
          <p>
            Revision {result.run.revision.revision_id}; result version {result.result_version}.
          </p>
          {result.verification ? (
            <p>
              Independent verification: {result.verification.status}. Critical errors:{" "}
              {result.verification.critical_errors.length}; warnings:{" "}
              {result.verification.warnings.length}.
            </p>
          ) : (
            <p>Independent verification is unavailable.</p>
          )}
          <ul className="plain">
            {result.findings.map((finding) => (
              <li className="card" key={finding.id}>
                <h3>
                  {finding.kind}: {finding.status}
                </h3>
                <p>
                  Observed: {finding.observed ?? "unavailable"}. Expected:{" "}
                  {finding.expected ?? "unavailable"}.
                </p>
                <p>
                  Rule: {finding.rule_id ?? "unavailable"} / {finding.rule_version ?? "unavailable"}
                  .
                </p>
                <p>{finding.trace}</p>
                <EvidenceList citations={finding.evidence ?? []} loadSource={loadSource} />
              </li>
            ))}
          </ul>
          {result.artifacts.length ? (
            <ul>
              {result.artifacts.map((artifact) => (
                <li key={artifact.artifact_id}>
                  <button disabled={busy} onClick={() => void download(artifact)}>
                    Download verified PDF
                  </button>
                  <p>
                    {artifact.page_count} pages. Artifact {artifact.artifact_id}.
                  </p>
                  <table aria-label="Artifact comparison contexts">
                    <thead>
                      <tr>
                        <th>Scope</th>
                        <th>Target</th>
                        <th>Comparable</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(artifact.schema_version === "artifact-manifest-v2"
                        ? artifact.contexts
                        : [artifact.context]
                      ).map((context) => (
                        <tr key={JSON.stringify(context)}>
                          <td>{context.scope}</td>
                          <td>{context.target_id}</td>
                          <td>{context.comparable_id}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </li>
              ))}
            </ul>
          ) : (
            <p>No published PDF is available for this result.</p>
          )}
        </>
      ) : null}
    </section>
  );
}
