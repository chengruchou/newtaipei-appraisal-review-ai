import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PdfPreview } from "@/privacy/PdfPreview";

const loading = vi.hoisted(() => vi.fn());
vi.mock("pdfjs-dist", () => ({ GlobalWorkerOptions: {}, getDocument: loading }));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function documentWith(secondPage: Promise<void>) {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    {} as CanvasRenderingContext2D,
  );
  const destroy = vi.fn().mockResolvedValue(undefined);
  const getPage = vi.fn((number: number) =>
    Promise.resolve({
      getViewport: () => ({ width: 100, height: 120 }),
      render: () => ({ promise: number === 1 ? Promise.resolve() : secondPage }),
    }),
  );
  loading.mockReturnValue({ promise: Promise.resolve({ numPages: 2, getPage }), destroy });
  return { getPage, destroy };
}

it("requires every sanitized PDF page to finish rendering before enabling approval", async () => {
  let finish!: () => void;
  const secondPage = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const { getPage, destroy } = documentWith(secondPage);
  const onReady = vi.fn();
  const view = render(<PdfPreview url="blob:verified-local-pdf" onReady={onReady} />);
  await waitFor(() => expect(getPage).toHaveBeenCalledTimes(2));
  expect(onReady).toHaveBeenCalledWith(false);
  expect(onReady).not.toHaveBeenCalledWith(true);
  finish();
  await waitFor(() => expect(onReady).toHaveBeenLastCalledWith(true));
  expect(screen.getByLabelText("Sanitized PDF page 1")).toBeInTheDocument();
  expect(screen.getByLabelText("Sanitized PDF page 2")).toBeInTheDocument();
  view.unmount();
  expect(destroy).toHaveBeenCalledTimes(1);
});

it("keeps approval blocked when a later sanitized page cannot render", async () => {
  let fail!: (error: Error) => void;
  const { getPage } = documentWith(
    new Promise<void>((_resolve, reject) => {
      fail = reject;
    }),
  );
  const onReady = vi.fn();
  render(<PdfPreview url="blob:verified-local-pdf" onReady={onReady} />);
  await waitFor(() => expect(getPage).toHaveBeenCalledTimes(2));
  fail(new Error("Page cannot render"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Approval remains blocked");
  expect(onReady).not.toHaveBeenCalledWith(true);
  expect(onReady).toHaveBeenLastCalledWith(false);
});
