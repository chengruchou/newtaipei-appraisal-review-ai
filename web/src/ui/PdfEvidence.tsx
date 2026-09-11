import { useEffect, useRef, useState } from "react";
import type { SourceCitation } from "@/api/client";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?worker&url";
import { evidenceBox } from "./evidenceGeometry";

export type SourceLoader = (citation: SourceCitation) => Promise<ArrayBuffer>;

/** Renders authorized bytes only. No remote URLs or caller-selected filesystem paths. */
export function PdfEvidence({
  citation,
  loadSource,
}: {
  citation: SourceCitation;
  loadSource: SourceLoader;
}) {
  const [opened, setOpened] = useState(false);
  const [status, setStatus] = useState("Loading the cited PDF page…");
  const [box, setBox] = useState<ReturnType<typeof evidenceBox>>(null);
  const [ready, setReady] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!opened) return;
    setReady(false);
    setBox(null);
    setStatus("Loading the cited PDF page…");
    let cancelled = false;
    let destroy: (() => void) | undefined;
    async function render() {
      try {
        const bytes = await loadSource(citation);
        if (cancelled) return;
        if (bytes.byteLength > 32 * 1024 * 1024) throw new Error("Source exceeds preview limit");
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const hash = [...new Uint8Array(digest)]
          .map((v) => v.toString(16).padStart(2, "0"))
          .join("");
        if (hash !== citation.content_hash) throw new Error("Source identity mismatch");
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
        const loading = pdfjs.getDocument({ data: bytes });
        destroy = () => {
          void loading.destroy();
        };
        if (cancelled) {
          destroy();
          return;
        }
        const pdf = await loading.promise;
        if (!Number.isInteger(citation.page) || citation.page < 1 || citation.page > pdf.numPages)
          throw new Error("Page unavailable");
        const page = await pdf.getPage(citation.page);
        if (cancelled || !canvas.current) return;
        // The parser emits unrotated CropBox-local coordinates; use that same page view.
        const viewport = page.getViewport({ scale: 1.25, rotation: 0 });
        canvas.current.width = viewport.width;
        canvas.current.height = viewport.height;
        const context = canvas.current.getContext("2d");
        if (!context) throw new Error("Canvas unavailable");
        await page.render({ canvasContext: context, canvas: canvas.current, viewport }).promise;
        if (cancelled) return;
        const [x0, y0, x1, y1] = page.view as [number, number, number, number];
        const located = evidenceBox(citation.bbox, x1 - x0, y1 - y0);
        setBox(located);
        setReady(true);
        setStatus(
          located
            ? "Cited field highlighted. Compare the source with the observation before answering."
            : "Page only: no valid field location is available. No area is highlighted.",
        );
      } catch {
        if (!cancelled)
          setStatus(
            "The exact source page could not be loaded or verified. Reload the task or ask for source access.",
          );
      }
    }
    void render();
    return () => {
      cancelled = true;
      destroy?.();
    };
  }, [opened, citation, loadSource]);

  return (
    <section aria-label={`Source page ${citation.page}`}>
      <button type="button" onClick={() => setOpened(!opened)}>
        {opened ? "Close source page" : `Open source page ${citation.page}`}
      </button>
      {opened ? (
        <>
          <p role="status">{status}</p>
          <div style={{ position: "relative", width: "100%", display: ready ? "block" : "none" }}>
            <canvas
              ref={canvas}
              style={{ width: "100%", display: "block" }}
              aria-label={`PDF page ${citation.page}: ${citation.region_id}`}
            />
            {box ? (
              <div
                data-testid="source-field-highlight"
                aria-label={`Cited field ${citation.region_id}`}
                style={{
                  position: "absolute",
                  ...box,
                  border: "2px solid #a33a00",
                  background: "#ffbc0033",
                  pointerEvents: "none",
                }}
              />
            ) : null}
          </div>
        </>
      ) : null}
    </section>
  );
}
