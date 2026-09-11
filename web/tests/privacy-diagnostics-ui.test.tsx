import { randomUUID } from "node:crypto";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { BridgeError, type LocalPrivacyClient } from "@/privacy/client";
import { PrivacyReviewPanel } from "@/privacy/PrivacyWorkbench";
import { OcrReview } from "@/privacy/OcrReview";
import type { LocalOcrReviewClient } from "@/privacy/ocr-review-client";
import { responseDiagnostic } from "@/privacy/diagnostics";
import { SOURCE } from "./privacy-fixtures";

afterEach(cleanup);
function failure(stage: string, code: string) {
  const reference = randomUUID();
  return {
    reference,
    error: new BridgeError(
      false,
      responseDiagnostic(
        409,
        new Headers({
          "X-Privacy-Request-Id": reference,
          "X-Privacy-Failure-Stage": stage,
          "X-Privacy-Failure-Code": code,
        }),
      ),
    ),
  };
}
it("shows a bounded final-download diagnostic without offering an unverified PDF", async () => {
  const { error, reference } = failure("final_read", "authority_denied");
  const mock = {
    sources: vi.fn().mockResolvedValue([]),
    restore: vi.fn().mockResolvedValue({ local_id: SOURCE }),
    restoredPdf: vi.fn().mockRejectedValue(error),
  };
  const user = userEvent.setup();
  render(<PrivacyReviewPanel client={mock as unknown as LocalPrivacyClient} />);
  await user.type(screen.getByLabelText("Authorized result identifier"), SOURCE);
  await user.click(screen.getByRole("button", { name: "Restore result through local bridge" }));
  const details = await screen.findByRole("complementary", {
    name: "Local privacy failure details",
  });
  expect(details).toHaveTextContent("HTTP status: 409");
  expect(details).toHaveTextContent("Local stage: final_read");
  expect(details).toHaveTextContent("Failure code: authority_denied");
  expect(details).toHaveTextContent(reference);
  expect(screen.queryByRole("link", { name: "Save restored PDF locally" })).not.toBeInTheDocument();
  expect(mock.restore).toHaveBeenCalledTimes(1);
  expect(mock.restoredPdf).toHaveBeenCalledTimes(1);
});
it("shows expired-review correlation while retaining the explicit restart requirement", async () => {
  const { error, reference } = failure("review_lifetime", "review_expired");
  const mock = { get: vi.fn().mockRejectedValue(error), restart: vi.fn() };
  render(
    <OcrReview
      client={mock as unknown as LocalOcrReviewClient}
      reviewId={SOURCE}
      resultId={SOURCE}
      onReady={vi.fn()}
      onRestarted={vi.fn()}
    />,
  );
  const details = await screen.findByRole("complementary", {
    name: "Local privacy failure details",
  });
  expect(details).toHaveTextContent("review_expired");
  expect(details).toHaveTextContent(reference);
  expect(screen.getByRole("button", { name: "Restart local OCR review" })).toBeEnabled();
  expect(mock.restart).not.toHaveBeenCalled();
});
