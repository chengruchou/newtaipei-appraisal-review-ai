import { useEffect, useRef, useState } from "react";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?worker&url";

/** Render locally verified bytes without depending on a browser PDF plugin. */
export function PdfPreview({ url, onReady }: { url: string; onReady: (ready: boolean) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const [message, setMessage] = useState("Rendering the exact sanitized PDF…");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const holder = container.current;
    let cancelled = false;
    let destroy: (() => void) | undefined;
    onReady(false);
    setFailed(false);
    setMessage("Rendering the exact sanitized PDF…");
    void (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        if (cancelled || !holder) return;
        pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
        const loading = pdfjs.getDocument({ url });
        destroy = () => {
          void loading.destroy();
        };
        const pdf = await loading.promise;
        for (let number = 1; number <= pdf.numPages; number += 1) {
          const page = await pdf.getPage(number);
          if (cancelled) return;
          const canvas = document.createElement("canvas");
          const viewport = page.getViewport({ scale: 1.25 });
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.style.width = "100%";
          canvas.style.display = "block";
          canvas.setAttribute("aria-label", `Sanitized PDF page ${number}`);
          holder.append(canvas);
          const context = canvas.getContext("2d");
          if (!context) throw new Error("Canvas unavailable");
          await page.render({ canvas, canvasContext: context, viewport }).promise;
          if (cancelled) return;
        }
        setMessage(
          `All ${pdf.numPages} sanitized preview pages rendered. Inspect each page before approving.`,
        );
        onReady(true);
      } catch {
        if (!cancelled) {
          setFailed(true);
          setMessage("The exact sanitized PDF could not be rendered. Approval remains blocked.");
          onReady(false);
        }
      }
    })();
    return () => {
      cancelled = true;
      destroy?.();
      holder?.replaceChildren();
    };
  }, [url, onReady]);
  return (
    <section title="Sanitized PDF preview" aria-label="Sanitized PDF preview">
      <p role={failed ? "alert" : undefined} aria-live="polite">
        {message}
      </p>
      <div ref={container} style={{ maxHeight: "32rem", overflow: "auto", border: "1px solid" }} />
    </section>
  );
}
