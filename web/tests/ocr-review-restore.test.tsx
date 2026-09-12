import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PrivacyReviewPanel } from "@/privacy/PrivacyWorkbench";
import { type LocalPrivacyClient } from "@/privacy/client";
import { OcrReviewRequired } from "@/privacy/ocr-review-client";
import { completedView, ocrView, REVIEW } from "./ocr-review-fixtures";

beforeEach(() => {
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:synthetic-restoration");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("separates published and candidate review receipts before downloading a restored PDF", async () => {
  const published = ocrView();
  const restored = ocrView();
  restored.stage = "restored";
  restored.receipts = completedView().receipts;
  const reviews = {
    get: vi
      .fn()
      .mockResolvedValueOnce(published)
      .mockResolvedValueOnce(restored)
      .mockResolvedValueOnce(completedView("restored")),
    page: vi.fn().mockResolvedValue(new Blob(["synthetic PNG"])),
    confirm: vi.fn().mockResolvedValue(completedView()),
  };
  const mock = {
    sources: vi.fn().mockResolvedValue([]),
    ocrReviews: reviews,
    restore: vi
      .fn()
      .mockRejectedValueOnce(new OcrReviewRequired(REVIEW))
      .mockRejectedValueOnce(new OcrReviewRequired(REVIEW))
      .mockResolvedValueOnce({ local_id: REVIEW, manifest: {} }),
    restoredPdf: vi.fn().mockResolvedValue(new Blob(["synthetic PDF"])),
  };
  const user = userEvent.setup();
  render(<PrivacyReviewPanel client={mock as unknown as LocalPrivacyClient} />);
  await user.type(screen.getByLabelText("Authorized result identifier"), REVIEW);
  const restore = screen.getByRole("button", { name: "Restore result through local bridge" });
  await user.click(restore);
  fireEvent.load(await screen.findByRole("img", { name: "OCR review full page 1" }));
  expect(restore).toBeDisabled();
  expect(screen.getByLabelText("Authorized result identifier")).toBeDisabled();
  expect(mock.restoredPdf).not.toHaveBeenCalled();
  expect(screen.queryByRole("link", { name: "Save restored PDF locally" })).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Exact visible reading"), "visible synthetic text");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Confirm this individual reading" }));
  await waitFor(() => expect(restore).toBeEnabled());
  expect(mock.restore).toHaveBeenCalledTimes(1);
  await user.click(restore);
  await screen.findByText("Review restored candidate OCR");
  expect(restore).toBeDisabled();
  expect(screen.getByLabelText("Exact visible reading")).toHaveValue("");
  expect(mock.restoredPdf).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Reload local OCR review" }));
  await waitFor(() => expect(restore).toBeEnabled());
  expect(mock.restore).toHaveBeenCalledTimes(2);
  await user.click(restore);
  expect(await screen.findByRole("link", { name: "Save restored PDF locally" })).toBeVisible();
  expect(mock.restoredPdf).toHaveBeenCalledTimes(1);
  expect(mock.restore).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole("region", { name: "Local visual OCR review" })).not.toBeInTheDocument();
});
