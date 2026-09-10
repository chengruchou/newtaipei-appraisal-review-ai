import { useEffect, useRef, useState } from "react";
import {
  BridgeError,
  LocalPrivacyClient,
  type ExportPreview,
  type ReviewSnapshot,
  versionOf,
} from "./client";
import { PageReview } from "./PageReview";
import { RegionEditor } from "./RegionEditor";

/** Mount separately from cloud review routes; the bridge session remains in component memory. */
export function PrivacyWorkbench({ bridgeBase }: { bridgeBase: string }) {
  const [token, setToken] = useState("");
  const [client, setClient] = useState<LocalPrivacyClient | null>(null);
  const [error, setError] = useState("");
  if (client)
    return (
      <>
        <button
          onClick={() => {
            setClient(null);
            setToken("");
          }}
        >
          Disconnect local privacy session
        </button>
        <PrivacyReviewPanel client={client} />
      </>
    );
  return (
    <section aria-label="Local privacy connection">
      <h1>Local privacy review</h1>
      <p>
        Original pages, reviewer text and restored documents stay with the separately configured
        local bridge.
      </p>
      <label>
        Local bridge session token
        <input
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
      </label>
      <button
        disabled={!token.trim() || !bridgeBase}
        onClick={() => {
          try {
            setClient(new LocalPrivacyClient({ baseUrl: bridgeBase, token: () => token }));
            setError("");
          } catch {
            setError("Configure an authorized loopback bridge before connecting.");
          }
        }}
      >
        Connect local bridge
      </button>
      {error ? <p role="alert">{error}</p> : null}
    </section>
  );
}

type ExportState = "none" | "preview" | "confirmed" | "attempted" | "transferred" | "unknown";

export function PrivacyReviewPanel({ client }: { client: LocalPrivacyClient }) {
  const [sources, setSources] = useState<string[]>([]);
  const [source, setSource] = useState("");
  const [snapshot, setSnapshot] = useState<ReviewSnapshot | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [needsReload, setNeedsReload] = useState(false);
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [exportState, setExportState] = useState<ExportState>("none");
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfViewed, setPdfViewed] = useState(false);
  const [exactConfirmed, setExactConfirmed] = useState(false);
  const [resultId, setResultId] = useState("");
  const [restoredUrl, setRestoredUrl] = useState<string | null>(null);
  const ownedUrls = useRef(new Set<string>());
  const uncertain = exportState === "unknown" || exportState === "attempted";
  const locked = busy || needsReload || uncertain;

  function localUrl(blob: Blob) {
    const url = URL.createObjectURL(blob);
    ownedUrls.current.add(url);
    return url;
  }
  function clearPreview() {
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl);
      ownedUrls.current.delete(pdfUrl);
    }
    setPdfUrl(null);
    setPdfViewed(false);
    setExactConfirmed(false);
    setPreview(null);
    setExportState("none");
  }
  useEffect(() => {
    let cancelled = false;
    void client
      .sources()
      .then((ids) => {
        if (!cancelled) {
          setSources(ids);
          setSource(ids[0] ?? "");
        }
      })
      .catch(() => {
        if (!cancelled)
          setMessage("The local source list could not be loaded. Reconnect to try again.");
      });
    const urls = ownedUrls.current;
    return () => {
      cancelled = true;
      urls.forEach((url) => URL.revokeObjectURL(url));
      urls.clear();
    };
  }, [client]);

  async function review(operation: () => Promise<ReviewSnapshot>) {
    clearPreview();
    setBusy(true);
    setMessage("");
    try {
      setSnapshot(await operation());
      setNeedsReload(false);
    } catch (error) {
      setNeedsReload(true);
      setMessage(
        error instanceof BridgeError
          ? error.message
          : "The review could not be updated. Reload it before continuing.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function prepare() {
    clearPreview();
    setBusy(true);
    setMessage("");
    try {
      const exact = await client.prepare();
      const pdf = await client.previewPdf(exact);
      setPreview(exact);
      setPdfUrl(localUrl(pdf));
      setExportState("preview");
    } catch (error) {
      setMessage(
        error instanceof BridgeError
          ? error.message
          : "The exact sanitized preview could not be prepared.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function confirmExport() {
    if (!preview || !pdfViewed || !exactConfirmed || exportState !== "preview") return;
    setBusy(true);
    setMessage("");
    try {
      await client.confirmExport(preview);
      setExportState("confirmed");
    } catch (error) {
      clearPreview();
      setMessage(
        error instanceof BridgeError
          ? error.message
          : "Preview confirmation failed. Prepare and inspect it again.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function transfer() {
    if (!preview || exportState !== "confirmed") return;
    setBusy(true);
    setExportState("attempted");
    setMessage("");
    try {
      await client.transfer(preview.preview_id);
      setExportState("transferred");
      setMessage("The bridge reports that this exact sanitized payload was transferred.");
    } catch {
      setExportState("unknown");
      setMessage(
        "Transfer did not return a confirmed result. Do not repeat it or create another export. Reconcile the attempted transfer with the local operator first.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function restore() {
    setBusy(true);
    setMessage("");
    if (restoredUrl) {
      URL.revokeObjectURL(restoredUrl);
      ownedUrls.current.delete(restoredUrl);
      setRestoredUrl(null);
    }
    try {
      const result = await client.restore(resultId);
      setRestoredUrl(localUrl(await client.restoredPdf(result)));
    } catch {
      setMessage(
        "The local result could not be authorized or restored. No restored PDF is available.",
      );
    } finally {
      setBusy(false);
    }
  }
  const allPagesReviewed =
    snapshot?.view.command.source.pages.every((p) =>
      snapshot.view.command.reviewed_pages.includes(p.number),
    ) ?? false;
  const key = snapshot
    ? `${snapshot.view.command.source.snapshot_id}:${snapshot.view.command.selection_revision}:${page}`
    : "none";

  return (
    <article>
      <h1>Review original material locally</h1>
      {message ? <p role="status">{message}</p> : null}
      <fieldset disabled={locked}>
        <legend>Authorized local sources</legend>
        <label>
          Source handle
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            {sources.map((identifier) => (
              <option key={identifier}>{identifier}</option>
            ))}
          </select>
        </label>
        <button
          disabled={!source}
          onClick={() => {
            setPage(1);
            void review(() => client.open(source));
          }}
        >
          Open selected source
        </button>
      </fieldset>
      <button disabled={busy || uncertain} onClick={() => void review(() => client.reload())}>
        Reload current local review
      </button>
      {snapshot ? (
        <>
          <p>
            Selection revision {snapshot.view.command.selection_revision}. Review state:{" "}
            {snapshot.view.state}. Every page must be viewed and explicitly marked reviewed.
          </p>
          <label>
            Original page
            <select
              disabled={locked}
              value={page}
              onChange={(e) => setPage(Number(e.target.value))}
            >
              {snapshot.view.command.source.pages.map((p) => (
                <option key={p.number} value={p.number}>
                  Page {p.number}
                  {snapshot.view.command.reviewed_pages.includes(p.number)
                    ? " — reviewed"
                    : " — awaiting review"}
                </option>
              ))}
            </select>
          </label>
          <PageReview
            key={key}
            client={client}
            snapshot={snapshot}
            page={page}
            disabled={locked}
            onReviewed={() => void review(() => client.markPage(versionOf(snapshot), page))}
          />
          <RegionEditor
            key={`regions:${key}`}
            client={client}
            snapshot={snapshot}
            page={page}
            disabled={locked}
            run={(operation) => void review(operation)}
          />
          <button
            disabled={
              locked || !allPagesReviewed || snapshot.view.state !== "awaiting_confirmation"
            }
            onClick={() => void review(() => client.confirmReview(snapshot))}
          >
            Confirm reviewed source and regions
          </button>
          <fieldset disabled={locked}>
            <legend>Exact sanitized export</legend>
            <p>This document upload contains only the sanitized PDF and signed manifest.</p>
            <button disabled={snapshot.view.state !== "confirmed"} onClick={() => void prepare()}>
              Prepare exact sanitized preview
            </button>
          </fieldset>
          {preview && pdfUrl ? (
            <section aria-label="Exact sanitized preview">
              <h2>Inspect exactly what will be transferred</h2>
              <iframe
                title="Sanitized PDF preview"
                src={pdfUrl}
                onLoad={() => setPdfViewed(true)}
                style={{ width: "100%", height: "32rem", border: "1px solid" }}
              />
              <h3>Sanitized reviewer text</h3>
              <pre>{preview.reviewer_text ?? "No reviewer text"}</pre>
              <h3>Sanitized manifest</h3>
              <pre style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
                {JSON.stringify(preview.manifest, null, 2)}
              </pre>
              <p>
                Exact payload digest: <code>{preview.payload_digest}</code>
              </p>
              <label>
                <input
                  type="checkbox"
                  disabled={locked || exportState !== "preview" || !pdfViewed}
                  checked={exactConfirmed}
                  onChange={(e) => setExactConfirmed(e.target.checked)}
                />
                I inspected this exact PDF, sanitized text and manifest and authorize this payload.
              </label>
              <button
                disabled={locked || !pdfViewed || !exactConfirmed || exportState !== "preview"}
                onClick={() => void confirmExport()}
              >
                Confirm this exact sanitized payload
              </button>
              <button
                disabled={busy || exportState !== "confirmed"}
                onClick={() => void transfer()}
              >
                Transfer confirmed payload once
              </button>
            </section>
          ) : null}
        </>
      ) : null}
      <fieldset disabled={busy || uncertain}>
        <legend>Restore an authorized result locally</legend>
        <label>
          Authorized result identifier
          <input value={resultId} onChange={(e) => setResultId(e.target.value)} />
        </label>
        <button
          disabled={!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(resultId)}
          onClick={() => void restore()}
        >
          Restore result through local bridge
        </button>
        {restoredUrl ? (
          <a href={restoredUrl} download="restored-local.pdf">
            Save restored PDF locally
          </a>
        ) : null}
      </fieldset>
    </article>
  );
}
