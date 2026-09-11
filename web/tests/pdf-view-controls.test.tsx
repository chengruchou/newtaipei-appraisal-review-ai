/** Mock-renderer component regressions only; not real PDF or browser acceptance. */
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SourceCitation } from "@/api/client";
import { ValueAuthority } from "@/ui/Authority";
import { LanguageProvider } from "@/ui/Language";
import { PdfEvidence } from "@/ui/PdfEvidence";
import { citation as baseCitation } from "./fixtures";

const loading = vi.hoisted(() => vi.fn());
vi.mock("pdfjs-dist", () => ({ GlobalWorkerOptions: {}, getDocument: loading }));

const bytes = new TextEncoder().encode("Unit renderer input, not an original PDF");
// The unit configuration disables CSS imports; exercise the actual stylesheet directly.
const workbenchCss = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../src/ui/styles.css"),
  "utf8",
);
let citation: SourceCitation;

function documentRenderer(width = 200, height = 100) {
  const tasks: Array<{ promise: Promise<void>; cancel: ReturnType<typeof vi.fn> }> = [];
  const page = {
    view: [50, 30, 50 + width, 30 + height],
    getViewport: vi.fn(({ scale }: { scale: number; rotation: number }) => ({
      width: width * scale,
      height: height * scale,
    })),
    render: vi.fn(() => {
      const task = { promise: Promise.resolve(), cancel: vi.fn() };
      tasks.push(task);
      return task;
    }),
  };
  const getPage = vi.fn().mockResolvedValue(page);
  const destroy = vi.fn().mockResolvedValue(undefined);
  loading.mockReturnValue({ promise: Promise.resolve({ numPages: 3, getPage }), destroy });
  return { page, tasks, getPage, destroy };
}

const loader = () => vi.fn().mockImplementation(() => Promise.resolve(bytes.slice().buffer));
const open = async () => {
  fireEvent.click(screen.getByRole("button", { name: "Open source page 3" }));
  await screen.findByText(/Cited field highlighted/);
};
const zoomOutput = () => screen.getByLabelText("Zoom relative to fit width");
const pageCanvas = () => screen.getByLabelText<HTMLCanvasElement>("PDF page 3: unit-region");

beforeEach(async () => {
  loading.mockReset();
  vi.stubGlobal("crypto", webcrypto);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    {} as CanvasRenderingContext2D,
  );
  citation = {
    ...baseCitation,
    document_id: "unit-document",
    page: 3,
    region_id: "unit-region",
    bbox: [10, 20, 120, 44],
    content_hash: Buffer.from(await webcrypto.subtle.digest("SHA-256", bytes)).toString("hex"),
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PDF view controls (unit regression)", () => {
  it("wraps an unbroken authority identifier while retaining the full text beside a zoomed PDF", async () => {
    documentRenderer();
    const subjectId =
      JSON.stringify([
        "individual",
        String.raw`unit-\u4e00\u4e8c\u4e09`.repeat(12),
        "unit-comparison",
      ]) + "/unit-factor:target";
    render(
      <>
        <style>{workbenchCss}</style>
        <ValueAuthority
          change={{
            schema_version: "service-v1",
            subject_id: subjectId,
            original: {
              schema_version: "service-v1",
              state: "missing",
              value: null,
              raw_text: "",
              unit: null,
              confidence: null,
              evidence: [],
            },
            proposed: null,
            corrected: null,
            corrected_by: null,
          }}
        />
        <PdfEvidence citation={citation} loadSource={loader()} />
      </>,
    );
    const title = screen.getByRole("heading", { level: 3, name: subjectId });
    expect(title.textContent).toBe(subjectId);
    expect(getComputedStyle(title).overflowWrap).toBe("anywhere");
    await open();
    for (let index = 0; index < 6; index++)
      fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await screen.findByTestId("source-field-highlight");
    expect(zoomOutput()).toHaveTextContent("400%");
    expect(title.textContent).toBe(subjectId);
    expect(
      getComputedStyle(screen.getByRole("region", { name: "Scrollable PDF page 3" })).overflow,
    ).toBe("auto");
    expect(pageCanvas().parentElement).toHaveStyle({ width: "400%" });
  });

  it("only opens on explicit keyboard action and exposes a focusable preview", async () => {
    const { getPage } = documentRenderer();
    const loadSource = loader();
    render(<PdfEvidence citation={citation} loadSource={loadSource} />);
    expect(loadSource).not.toHaveBeenCalled();
    expect(loading).not.toHaveBeenCalled();
    const user = userEvent.setup();
    await user.tab();
    const toggle = screen.getByRole("button", { name: "Open source page 3" });
    expect(toggle).toHaveFocus();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.keyboard("{Enter}");
    await screen.findByText(/Cited field highlighted/);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(toggle.getAttribute("aria-controls")!)).toBeInTheDocument();
    expect(loadSource).toHaveBeenCalledExactlyOnceWith(citation);
    expect(getPage).toHaveBeenCalledExactlyOnceWith(3);
    expect(zoomOutput()).toHaveTextContent("100%");
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    await user.tab();
    expect(screen.getByRole("button", { name: "Zoom in" })).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(pageCanvas().width).toBe(375));
    await user.tab();
    expect(screen.getByRole("button", { name: "Fit width" })).toHaveFocus();
    await user.tab();
    const viewport = screen.getByRole("region", { name: "Scrollable PDF page 3" });
    expect(viewport).toHaveFocus();
    expect(viewport).toHaveAccessibleDescription(/arrow keys or scroll/);
    expect(loadSource).toHaveBeenCalledTimes(1);
  });

  it("redraws the same verified page and preserves CropBox-relative highlights at bounded zoom", async () => {
    const { page, getPage } = documentRenderer();
    const loadSource = loader();
    const original = structuredClone(citation);
    render(<PdfEvidence citation={citation} loadSource={loadSource} />);
    await open();
    expect(pageCanvas().width).toBe(250);
    for (let zoom = 150; zoom <= 400; zoom += 50) {
      fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
      await waitFor(() => expect(pageCanvas().width).toBe((250 * zoom) / 100));
      expect(zoomOutput()).toHaveTextContent(`${zoom}%`);
      expect(await screen.findByTestId("source-field-highlight")).toHaveStyle({
        left: "5%",
        top: "56%",
        width: "55%",
        height: "24%",
      });
      expect(pageCanvas().parentElement).toHaveStyle({ width: `${zoom}%` });
    }
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(zoomOutput()).toHaveTextContent("400%");
    expect(page.render).toHaveBeenCalledTimes(7);
    expect(page.getViewport.mock.calls.every(([args]) => args.rotation === 0)).toBe(true);
    expect(loadSource).toHaveBeenCalledTimes(1);
    expect(loading).toHaveBeenCalledTimes(1);
    expect(getPage).toHaveBeenCalledExactlyOnceWith(3);
    expect(citation).toEqual(original);
  });

  it("bounds zoom-out and resets both preview scroll axes with fit width", async () => {
    documentRenderer();
    const loadSource = loader();
    render(<PdfEvidence citation={citation} loadSource={loadSource} />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await waitFor(() => expect(pageCanvas().width).toBe(375));
    const viewport = screen.getByRole("region", { name: "Scrollable PDF page 3" });
    viewport.scrollLeft = 180;
    viewport.scrollTop = 90;
    fireEvent.click(screen.getByRole("button", { name: "Fit width" }));
    await waitFor(() => expect(pageCanvas().width).toBe(250));
    expect(viewport.scrollLeft).toBe(0);
    expect(viewport.scrollTop).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(zoomOutput()).toHaveTextContent("100%");
    expect(loadSource).toHaveBeenCalledTimes(1);
  });

  it("cancels an obsolete render without publishing its later completion", async () => {
    const { page } = documentRenderer();
    render(<PdfEvidence citation={citation} loadSource={loader()} />);
    await open();
    const pending: Array<{ finish: () => void; cancel: ReturnType<typeof vi.fn> }> = [];
    page.render.mockImplementation(() => {
      let finish!: () => void;
      const promise = new Promise<void>((resolve) => {
        finish = resolve;
      });
      const cancel = vi.fn();
      pending.push({ finish, cancel });
      return { promise, cancel };
    });
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await waitFor(() => expect(pending).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(pending[0]!.cancel).toHaveBeenCalledTimes(1);
    await act(() => Promise.resolve(pending[0]!.finish()));
    expect(screen.queryByTestId("source-field-highlight")).not.toBeInTheDocument();
    expect(pageCanvas()).not.toBeVisible();
    await act(() => Promise.resolve(pending[1]!.finish()));
    expect(screen.getByTestId("source-field-highlight")).toBeVisible();
    expect(zoomOutput()).toHaveTextContent("100%");
  });

  it("destroys the open document on close and reauthorizes only on another explicit open", async () => {
    const { destroy, tasks } = documentRenderer();
    const loadSource = loader();
    render(<PdfEvidence citation={citation} loadSource={loadSource} />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await waitFor(() => expect(pageCanvas().width).toBe(375));
    fireEvent.click(screen.getByRole("button", { name: "Close source page" }));
    expect(screen.queryByLabelText("PDF page 3: unit-region")).not.toBeInTheDocument();
    expect(destroy).toHaveBeenCalledTimes(1);
    expect(tasks.at(-1)!.cancel).toHaveBeenCalledTimes(1);
    expect(loadSource).toHaveBeenCalledTimes(1);
    await open();
    expect(loadSource).toHaveBeenCalledTimes(2);
    expect(zoomOutput()).toHaveTextContent("100%");
  });

  it("retains zoom across identical polling data without refetching", async () => {
    documentRenderer();
    const loadSource = loader();
    const view = render(<PdfEvidence citation={citation} loadSource={loadSource} />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await waitFor(() => expect(pageCanvas().width).toBe(375));
    view.rerender(<PdfEvidence citation={structuredClone(citation)} loadSource={loadSource} />);
    expect(zoomOutput()).toHaveTextContent("150%");
    expect(pageCanvas()).toBeVisible();
    expect(loadSource).toHaveBeenCalledTimes(1);
  });

  it.each(["citation", "loader"])(
    "clears an open preview when the %s changes without auto-fetch",
    async (change) => {
      const { destroy } = documentRenderer();
      const loadSource = loader();
      const otherLoader = loader();
      const view = render(<PdfEvidence citation={citation} loadSource={loadSource} />);
      await open();
      view.rerender(
        <PdfEvidence
          citation={
            change === "citation" ? { ...citation, version: "unit-next-version" } : citation
          }
          loadSource={change === "loader" ? otherLoader : loadSource}
        />,
      );
      expect(screen.queryByLabelText("PDF page 3: unit-region")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Open source page 3" })).toHaveAttribute(
        "aria-expanded",
        "false",
      );
      expect(destroy).toHaveBeenCalledTimes(1);
      expect(loadSource).toHaveBeenCalledTimes(1);
      expect(otherLoader).not.toHaveBeenCalled();
      view.rerender(<PdfEvidence citation={citation} loadSource={loadSource} />);
      expect(screen.queryByLabelText("PDF page 3: unit-region")).not.toBeInTheDocument();
      expect(loadSource).toHaveBeenCalledTimes(1);
      await open();
      expect(loadSource).toHaveBeenCalledTimes(2);
      expect(zoomOutput()).toHaveTextContent("100%");
    },
  );

  it("does not parse or reopen a late source response after closing", async () => {
    documentRenderer();
    let finish!: (value: ArrayBuffer) => void;
    const loadSource = vi.fn(
      () =>
        new Promise<ArrayBuffer>((resolve) => {
          finish = resolve;
        }),
    );
    render(<PdfEvidence citation={citation} loadSource={loadSource} />);
    fireEvent.click(screen.getByRole("button", { name: "Open source page 3" }));
    fireEvent.click(screen.getByRole("button", { name: "Close source page" }));
    await act(() => Promise.resolve(finish(bytes.slice().buffer)));
    expect(loading).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("PDF page 3: unit-region")).not.toBeInTheDocument();
    expect(loadSource).toHaveBeenCalledTimes(1);
  });

  it("hides the previous canvas and highlight if a zoom render fails", async () => {
    const { page } = documentRenderer();
    const loadSource = loader();
    render(<PdfEvidence citation={citation} loadSource={loadSource} />);
    await open();
    page.render.mockReturnValueOnce({
      promise: Promise.reject(new Error("Render failed")),
      cancel: vi.fn(),
    });
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await screen.findByText(/could not be loaded or verified/);
    expect(pageCanvas()).not.toBeVisible();
    expect(screen.queryByTestId("source-field-highlight")).not.toBeInTheDocument();
    expect(loadSource).toHaveBeenCalledTimes(1);
  });

  it.each(["denied", "hash"])(
    "does not offer zoom or render a source on %s failure",
    async (failure) => {
      documentRenderer();
      const loadSource =
        failure === "denied" ? vi.fn().mockRejectedValue(new Error("Denied")) : loader();
      render(
        <PdfEvidence
          citation={failure === "hash" ? { ...citation, content_hash: "0".repeat(64) } : citation}
          loadSource={loadSource}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Open source page 3" }));
      await screen.findByText(/could not be loaded or verified/);
      expect(loading).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Zoom in" })).not.toBeInTheDocument();
      expect(pageCanvas()).not.toBeVisible();
    },
  );

  it.each([0, 1.5, 4])(
    "preserves one-based page authorization for invalid page %s",
    async (page) => {
      const { getPage } = documentRenderer();
      render(<PdfEvidence citation={{ ...citation, page }} loadSource={loader()} />);
      fireEvent.click(screen.getByRole("button", { name: `Open source page ${page}` }));
      await screen.findByText(/could not be loaded or verified/);
      expect(getPage).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "Zoom in" })).not.toBeInTheDocument();
    },
  );

  it("does not invent a highlight for missing field geometry after zoom", async () => {
    documentRenderer();
    render(<PdfEvidence citation={{ ...citation, bbox: [0, 0, 0, 0] }} loadSource={loader()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open source page 3" }));
    await screen.findByText(/no valid field location/);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await waitFor(() => expect(pageCanvas().width).toBe(375));
    expect(screen.queryByTestId("source-field-highlight")).not.toBeInTheDocument();
    expect(screen.getByText(/no valid field location/)).toBeInTheDocument();
  });

  it("caps the rendering allocation for oversized page dimensions", async () => {
    documentRenderer(50_000, 25_000);
    render(<PdfEvidence citation={citation} loadSource={loader()} />);
    await open();
    for (let index = 0; index < 6; index++)
      fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await screen.findByText(/Cited field highlighted/);
    expect(zoomOutput()).toHaveTextContent("400%");
    expect(pageCanvas().width).toBeLessThanOrEqual(8192);
    expect(pageCanvas().height).toBeLessThanOrEqual(8192);
    expect(pageCanvas().width * pageCanvas().height).toBeLessThanOrEqual(16_000_000);
  });

  it("provides Chinese zoom controls and scroll guidance", async () => {
    documentRenderer();
    render(
      <LanguageProvider language="zh">
        <PdfEvidence citation={citation} loadSource={loader()} />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "開啟來源第 3 頁" }));
    await screen.findByText(/已標示引用區域/);
    expect(screen.getByRole("group", { name: "PDF 縮放" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "放大" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "符合頁寬" })).toBeEnabled();
    expect(screen.getByLabelText("相對頁寬縮放比例")).toHaveTextContent("100%");
    expect(screen.getByRole("region", { name: "可捲動 PDF 第 3 頁" })).toHaveAccessibleDescription(
      /方向鍵或捲動/,
    );
  });
});
