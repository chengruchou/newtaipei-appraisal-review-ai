import { useEffect, useState } from "react";
import { evidenceBox } from "@/ui/evidenceGeometry";
import type { LocalPrivacyClient, ReviewSnapshot } from "./client";

export function PageReview({
  client,
  snapshot,
  page,
  disabled,
  onReviewed,
}: {
  client: LocalPrivacyClient;
  snapshot: ReviewSnapshot;
  page: number;
  disabled: boolean;
  onReviewed: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let owned: string | undefined;
    void client
      .page(page)
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
  }, [client, page]);
  const dimensions = snapshot.view.command.source.pages.find((p) => p.number === page);
  return (
    <section aria-label={`Original privacy page ${page}`}>
      <h3>Original page {page} — local only</h3>
      {failed ? (
        <p role="alert">The original page could not be retrieved. It cannot be marked reviewed.</p>
      ) : null}
      {url && dimensions ? (
        <div style={{ position: "relative" }}>
          <img
            src={url}
            alt={`Original source page ${page}`}
            onLoad={() => setLoaded(true)}
            onError={() => {
              setLoaded(false);
              setFailed(true);
            }}
            style={{ width: "100%", display: "block" }}
          />
          {loaded
            ? snapshot.view.command.selections
                .filter((s) => s.candidate.region.page === page && s.disposition === "redact")
                .map((s) => {
                  const box = evidenceBox(
                    s.candidate.region.bbox,
                    dimensions.width,
                    dimensions.height,
                  );
                  return box ? (
                    <div
                      key={s.candidate.candidate_id}
                      aria-label={`Redaction region ${s.candidate.category}`}
                      style={{
                        position: "absolute",
                        ...box,
                        border: "2px solid #a33a00",
                        background: "#ffbc0033",
                        pointerEvents: "none",
                      }}
                    />
                  ) : null;
                })
            : null}
        </div>
      ) : null}
      <button type="button" disabled={disabled || !loaded || failed} onClick={onReviewed}>
        I reviewed this page and its marked regions
      </button>
    </section>
  );
}
