import { useEffect, useState } from "react";
import { evidenceBox } from "@/ui/evidenceGeometry";
import {
  currentOcrReceipt,
  validOcrReading,
  type LocalOcrReviewClient,
  type OcrItem,
  type OcrReviewView,
} from "./ocr-review-client";

export function OcrReview({
  client,
  reviewId,
  resultId,
  onReady,
  onRestarted,
}: {
  client: LocalOcrReviewClient;
  reviewId: string;
  resultId: string;
  onReady: (ready: boolean) => void;
  onRestarted: () => void;
}) {
  const [view, setView] = useState<OcrReviewView | null>(null);
  const [error, setError] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartFailed, setRestartFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    onReady(false);
    setView(null);
    setError(false);
    void client
      .get(reviewId)
      .then((current) => {
        if (!cancelled) setView(current);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [client, reviewId, revision, onReady]);

  async function restart() {
    if (confirming || restarting || restartFailed) return;
    onReady(false);
    setRestarting(true);
    setView(null);
    try {
      await client.restart(reviewId, resultId);
      onRestarted();
    } catch {
      setRestartFailed(true);
    } finally {
      setRestarting(false);
    }
  }
  return (
    <section aria-label="Local visual OCR review">
      {error ? (
        <p role="alert">The local OCR review could not be verified. Reload before continuing.</p>
      ) : null}
      {!view && !error && !restarting && !restartFailed ? (
        <p role="status">Loading the exact local OCR review…</p>
      ) : null}
      {restarting ? (
        <p role="status">Revoking the previous local review and retaining its evidence…</p>
      ) : null}
      {restartFailed ? (
        <p role="alert">
          The restart did not return a verified result. Reconnect to reconcile the local review
          before continuing.
        </p>
      ) : null}
      {view ? (
        <ReviewStage
          key={`${view.review_digest}:${revision}`}
          client={client}
          initial={view}
          onReady={onReady}
          onConfirming={setConfirming}
        />
      ) : null}
      <button
        disabled={confirming || restarting || restartFailed}
        onClick={() => {
          onReady(false);
          setView(null);
          setRevision((value) => value + 1);
        }}
      >
        Reload local OCR review
      </button>
      <p>
        Restart invalidates this review's confirmations and retains its evidence. Use it for an
        expired review or an incorrect reading. Then restore the result explicitly to begin a fresh
        review.
      </p>
      <button
        disabled={confirming || restarting || restartFailed || (!view && !error)}
        onClick={() => void restart()}
      >
        Restart local OCR review
      </button>
    </section>
  );
}

function ReviewStage({
  client,
  initial,
  onReady,
  onConfirming,
}: {
  client: LocalOcrReviewClient;
  initial: OcrReviewView;
  onReady: (ready: boolean) => void;
  onConfirming: (busy: boolean) => void;
}) {
  const [view, setView] = useState(initial);
  const [page, setPage] = useState(
    initial.items.find((item) => item.confirmed_reading === null)?.page ??
      initial.items[0]?.page ??
      initial.pages[0]!.number,
  );
  const [itemId, setItemId] = useState(
    initial.items.find((item) => item.confirmed_reading === null)?.item_id ??
      initial.items[0]?.item_id ??
      "",
  );
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const receipt = currentOcrReceipt(view);
  const item = view.items.find((entry) => entry.item_id === itemId && entry.page === page);
  const remaining = view.items.filter((entry) => entry.confirmed_reading === null).length;
  useEffect(() => {
    onReady(receipt !== undefined && !failed && !busy);
    if (!receipt) return;
    const timer = setTimeout(
      () => onReady(false),
      Math.min(2_147_483_647, Math.max(0, Date.parse(receipt.expires_at) - Date.now())),
    );
    return () => clearTimeout(timer);
  }, [receipt, failed, busy, onReady]);

  async function confirm(reading: string) {
    if (!item || busy || failed) return;
    setBusy(true);
    onConfirming(true);
    onReady(false);
    try {
      setView(await client.confirm(view, item.item_id, reading));
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
      onConfirming(false);
    }
  }
  return (
    <>
      <h2>
        {view.stage === "published"
          ? "Review published document OCR"
          : "Review restored candidate OCR"}
      </h2>
      <p>
        Inspect the actual page and region, then transcribe each required item separately. Original
        OCR measurements remain unchanged. These readings do not grant business approval.
      </p>
      <p>
        {remaining} of {view.items.length} items await an individual confirmation. All{" "}
        {view.observations.length} original observations are retained.
      </p>
      <p>
        Input SHA-256: <code style={{ overflowWrap: "anywhere" }}>{view.input_sha256}</code>
      </p>
      {failed ? (
        <p role="alert">
          The confirmation did not return a verified result. Reload this local review to reconcile
          its current receipts before continuing.
        </p>
      ) : null}
      <label>
        OCR review page
        <select
          aria-label="OCR review page"
          value={page}
          disabled={busy}
          onChange={(event) => {
            const next = Number(event.target.value);
            setPage(next);
            setItemId(
              view.items.find((entry) => entry.page === next && entry.confirmed_reading === null)
                ?.item_id ??
                view.items.find((entry) => entry.page === next)?.item_id ??
                "",
            );
          }}
        >
          {view.pages.map((entry) => (
            <option key={entry.number} value={entry.number}>
              Page {entry.number}
            </option>
          ))}
        </select>
      </label>
      <label>
        Required OCR item
        <select
          aria-label="Required OCR item"
          value={itemId}
          disabled={busy}
          onChange={(event) => setItemId(event.target.value)}
        >
          {!view.items.some((entry) => entry.page === page) ? (
            <option value="">No required items on this page</option>
          ) : null}
          {view.items
            .filter((entry) => entry.page === page)
            .map((entry) => (
              <option key={entry.item_id} value={entry.item_id}>
                {entry.kind === "placeholder" ? "Placeholder region" : "OCR observation"}{" "}
                {entry.item_id}
                {entry.confirmed_reading === null ? " — awaiting review" : " — confirmed"}
              </option>
            ))}
        </select>
      </label>
      <ReviewPage
        key={`${view.review_digest}:${page}`}
        client={client}
        view={view}
        page={page}
        item={item}
        disabled={busy || failed}
        confirm={confirm}
      />
      <h3>All original observations on page {page}</h3>
      <table>
        <thead>
          <tr>
            <th>Observation</th>
            <th>Original OCR text</th>
            <th>Original confidence</th>
            <th>Region (PDF points)</th>
            <th>Review item</th>
          </tr>
        </thead>
        <tbody>
          {view.observations
            .filter((entry) => entry.page === page)
            .map((entry) => (
              <tr key={entry.observation_id}>
                <td>{entry.observation_id}</td>
                <td style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                  {entry.raw_text || "(empty)"}
                </td>
                <td>{entry.confidence === null ? "Missing" : String(entry.confidence)}</td>
                <td>{entry.bbox.join(", ")}</td>
                <td>{entry.review_item_id ?? "No individual confirmation required"}</td>
              </tr>
            ))}
        </tbody>
      </table>
      {receipt ? (
        <p role="status">
          This stage has an exact local review receipt. Use Restore result through local bridge to
          continue. Receipt: <code>{receipt.receipt_id}</code>
        </p>
      ) : null}
      {view.receipts.length ? (
        <details>
          <summary>Local review receipts ({view.receipts.length})</summary>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {JSON.stringify(view.receipts, null, 2)}
          </pre>
        </details>
      ) : null}
    </>
  );
}

function ReviewPage({
  client,
  view,
  page,
  item,
  disabled,
  confirm,
}: {
  client: LocalOcrReviewClient;
  view: OcrReviewView;
  page: number;
  item: OcrItem | undefined;
  disabled: boolean;
  confirm: (reading: string) => Promise<void>;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const dimensions = view.pages.find((entry) => entry.number === page)!;
  // Confirmations change receipts only; the mounted stage fixes these image bytes.
  const [imageView] = useState(view);
  useEffect(() => {
    let cancelled = false;
    let owned: string | undefined;
    void client
      .page(imageView, page)
      .then((blob) => {
        if (!cancelled) {
          owned = URL.createObjectURL(blob);
          setUrl(owned);
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (owned) URL.revokeObjectURL(owned);
    };
  }, [client, imageView, page]);
  const box = item ? evidenceBox(item.bbox, dimensions.width, dimensions.height) : null;
  return (
    <>
      {failed ? (
        <p role="alert">
          The exact page image could not be loaded. No item on this page can be confirmed.
        </p>
      ) : null}
      {url ? (
        <>
          <div style={{ position: "relative", maxWidth: "60rem" }}>
            <img
              src={url}
              alt={`OCR review full page ${page}`}
              onLoad={() => setLoaded(true)}
              onError={() => {
                setLoaded(false);
                setFailed(true);
              }}
              style={{ display: "block", width: "100%" }}
            />
            {loaded && box ? (
              <div
                aria-label="Selected OCR region"
                style={{
                  position: "absolute",
                  ...box,
                  border: "2px solid #a33a00",
                  pointerEvents: "none",
                }}
              />
            ) : null}
          </div>
          {item && loaded && !failed ? (
            <svg
              role="img"
              aria-label={`OCR review region crop ${item.item_id}`}
              viewBox={`${item.bbox[0]} ${dimensions.height - item.bbox[3]} ${item.bbox[2] - item.bbox[0]} ${item.bbox[3] - item.bbox[1]}`}
              style={{
                display: "block",
                width: "100%",
                maxWidth: "60rem",
                maxHeight: "20rem",
                border: "1px solid currentColor",
              }}
            >
              <image href={url} width={dimensions.width} height={dimensions.height} />
            </svg>
          ) : null}
        </>
      ) : null}
      {item ? (
        <ItemTranscription
          key={item.item_id}
          item={item}
          disabled={disabled || !loaded || failed}
          confirm={confirm}
        />
      ) : null}
    </>
  );
}

function ItemTranscription({
  item,
  disabled,
  confirm,
}: {
  item: OcrItem;
  disabled: boolean;
  confirm: (reading: string) => Promise<void>;
}) {
  const [reading, setReading] = useState("");
  const [inspected, setInspected] = useState(false);
  if (item.confirmed_reading !== null)
    return (
      <p>
        Confirmed local reading:{" "}
        <span style={{ whiteSpace: "pre-wrap" }}>{item.confirmed_reading}</span>
      </p>
    );
  return (
    <fieldset disabled={disabled}>
      <legend>Individual visual transcription</legend>
      {item.kind === "placeholder" ? (
        <p>
          Expected placeholder: <code>{item.expected_text}</code>. Enter it only if these exact
          characters are visible in this region.
        </p>
      ) : null}
      <label>
        Exact visible reading
        <textarea
          value={reading}
          maxLength={4096}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            setReading(event.target.value);
            setInspected(false);
          }}
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={inspected}
          onChange={(event) => setInspected(event.target.checked)}
        />
        I inspected this page and region and transcribed this item exactly.
      </label>
      <button
        disabled={
          !inspected ||
          !validOcrReading(reading) ||
          (item.kind === "placeholder" && reading !== item.expected_text)
        }
        onClick={() => void confirm(reading)}
      >
        Confirm this individual reading
      </button>
    </fieldset>
  );
}
