import { useEffect, useId, useRef, useState } from "react";
import type { PDFPageProxy } from "pdfjs-dist";
import type { SourceCitation } from "@/api/client";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?worker&url";
import { useText } from "./Language";
import { evidenceBox } from "./evidenceGeometry";

export type SourceLoader = (citation: SourceCitation) => Promise<ArrayBuffer>;

type LoadedPage = {
  page: PDFPageProxy;
  citation: SourceCitation;
  loadSource: SourceLoader;
};

const MIN_ZOOM = 100;
const MAX_ZOOM = 400;
const ZOOM_STEP = 50;

/** Renders authorized bytes only. No remote URLs or caller-selected filesystem paths. */
export function PdfEvidence({
  citation,
  loadSource,
}: {
  citation: SourceCitation;
  loadSource: SourceLoader;
}) {
  const t = useText();
  const [request, setRequest] = useState<{
    citation: SourceCitation;
    key: string;
    loadSource: SourceLoader;
  } | null>(null);
  const [status, setStatus] = useState("Loading the cited PDF page…");
  const [box, setBox] = useState<ReturnType<typeof evidenceBox>>(null);
  const [loaded, setLoaded] = useState<LoadedPage | null>(null);
  const [rendered, setRendered] = useState<{ source: LoadedPage; zoom: number } | null>(null);
  const [zoom, setZoom] = useState(MIN_ZOOM);
  const canvas = useRef<HTMLCanvasElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const previewId = useId();
  const helpId = useId();
  const opened = request?.key === JSON.stringify(citation) && request.loadSource === loadSource;
  const source =
    opened && loaded?.citation === request.citation && loaded.loadSource === loadSource
      ? loaded
      : null;
  const ready = source !== null && rendered?.source === source && rendered.zoom === zoom;

  useEffect(() => {
    if (!opened || !request) {
      setRequest(null);
      setLoaded(null);
      setRendered(null);
      return;
    }
    const { citation, loadSource } = request;
    setLoaded(null);
    setRendered(null);
    setBox(null);
    setZoom(MIN_ZOOM);
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
        if (cancelled) return;
        const pdfjs = await import("pdfjs-dist");
        if (cancelled) return;
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
        if (cancelled) return;
        const [x0, y0, x1, y1] = page.view as [number, number, number, number];
        setBox(evidenceBox(citation.bbox, x1 - x0, y1 - y0));
        setLoaded({ page, citation, loadSource });
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
  }, [opened, request]);

  useEffect(() => {
    if (!source || !canvas.current) return;
    let cancelled = false;
    let task: ReturnType<PDFPageProxy["render"]> | undefined;
    setRendered(null);
    setStatus("Rendering the cited PDF page…");
    async function renderPage() {
      try {
        const target = canvas.current;
        if (!target || !source) return;
        // Keep the same unrotated CropBox view at every zoom level.
        const base = source.page.getViewport({ scale: 1, rotation: 0 });
        if (![base.width, base.height].every((value) => Number.isFinite(value) && value > 0))
          throw new Error("Invalid page dimensions");
        const scale = Math.min(
          (1.25 * zoom) / 100,
          Math.sqrt(16_000_000 / (base.width * base.height)),
          8192 / base.width,
          8192 / base.height,
        );
        const viewport = source.page.getViewport({ scale, rotation: 0 });
        target.width = Math.max(1, Math.floor(viewport.width));
        target.height = Math.max(1, Math.floor(viewport.height));
        const context = target.getContext("2d");
        if (!context) throw new Error("Canvas unavailable");
        task = source.page.render({ canvasContext: context, canvas: target, viewport });
        await task.promise;
        if (cancelled) return;
        setRendered({ source, zoom });
        setStatus(
          box
            ? "Cited field highlighted. Compare the source with the observation before answering."
            : "Page only: no valid field location is available. No area is highlighted.",
        );
      } catch {
        if (!cancelled) {
          setRendered(null);
          setStatus(
            "The exact source page could not be loaded or verified. Reload the task or ask for source access.",
          );
        }
      }
    }
    void renderPage();
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [source, zoom, box]);

  function fitWidth() {
    setZoom(MIN_ZOOM);
    if (scroll.current) {
      scroll.current.scrollLeft = 0;
      scroll.current.scrollTop = 0;
    }
  }

  return (
    <section
      className="pdf-evidence"
      aria-label={t(`Source page ${citation.page}`, `來源第 ${citation.page} 頁`)}
    >
      <button
        type="button"
        aria-expanded={opened}
        aria-controls={previewId}
        onClick={() => {
          setLoaded(null);
          setRendered(null);
          setRequest(opened ? null : { citation, key: JSON.stringify(citation), loadSource });
        }}
      >
        {opened
          ? t("Close source page", "關閉來源頁")
          : t(`Open source page ${citation.page}`, `開啟來源第 ${citation.page} 頁`)}
      </button>
      {opened ? (
        <div id={previewId}>
          <p role="status">
            {t(
              status,
              status.startsWith("Loading")
                ? "正在載入引用的 PDF 頁面…"
                : status.startsWith("Rendering")
                  ? "正在繪製引用的 PDF 頁面…"
                  : status.startsWith("Cited field")
                    ? "已標示引用區域，請比對原觀察值後再回覆。"
                    : status.startsWith("Page only")
                      ? "沒有有效定位資料，只顯示頁面，不產生虛構框線。"
                      : "來源頁無法載入或驗證，請重新讀取任務或確認來源權限。",
            )}
          </p>
          {source ? (
            <>
              <div className="pdf-controls" role="group" aria-label={t("PDF zoom", "PDF 縮放")}>
                <button
                  type="button"
                  disabled={zoom === MIN_ZOOM}
                  onClick={() => setZoom((value) => Math.max(MIN_ZOOM, value - ZOOM_STEP))}
                >
                  {t("Zoom out", "縮小")}
                </button>
                <output
                  aria-live="polite"
                  aria-label={t("Zoom relative to fit width", "相對頁寬縮放比例")}
                >
                  {zoom}%
                </output>
                <button
                  type="button"
                  disabled={zoom === MAX_ZOOM}
                  onClick={() => setZoom((value) => Math.min(MAX_ZOOM, value + ZOOM_STEP))}
                >
                  {t("Zoom in", "放大")}
                </button>
                <button type="button" onClick={fitWidth}>
                  {t("Fit width", "符合頁寬")}
                </button>
              </div>
              <p className="small muted" id={helpId}>
                {t(
                  "100% fits the preview width. At higher zoom, focus the preview and use arrow keys or scroll to inspect the page.",
                  "100% 符合預覽頁寬。放大後可聚焦預覽區，以方向鍵或捲動檢視頁面。",
                )}
              </p>
            </>
          ) : null}
          <div
            ref={scroll}
            className="pdf-viewport"
            role="region"
            tabIndex={0}
            aria-label={t(
              `Scrollable PDF page ${citation.page}`,
              `可捲動 PDF 第 ${citation.page} 頁`,
            )}
            aria-describedby={source ? helpId : undefined}
            style={{ display: source ? "block" : "none" }}
          >
            <div
              className="pdf-page"
              style={{ width: `${zoom}%`, visibility: ready ? "visible" : "hidden" }}
            >
              <canvas
                ref={canvas}
                aria-label={t(
                  `PDF page ${citation.page}: ${citation.region_id}`,
                  `PDF 第 ${citation.page} 頁：${citation.region_id}`,
                )}
              />
              {box && ready ? (
                <div
                  data-testid="source-field-highlight"
                  aria-label={t(
                    `Cited field ${citation.region_id}`,
                    `引用區域 ${citation.region_id}`,
                  )}
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
          </div>
        </div>
      ) : null}
    </section>
  );
}
