import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OcrReview } from "@/privacy/OcrReview";
import { type LocalOcrReviewClient } from "@/privacy/ocr-review-client";
import { completedView, ITEM, ocrView, PLACEHOLDER, REVIEW } from "./ocr-review-fixtures";

const revoke = vi.fn();
beforeEach(() => {
  revoke.mockClear();
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:synthetic-ocr-page");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(revoke);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function mockClient() {
  return {
    get: vi.fn().mockResolvedValue(ocrView()),
    page: vi.fn().mockResolvedValue(new Blob(["synthetic image"])),
    confirm: vi.fn(),
    restart: vi.fn().mockResolvedValue(undefined),
  };
}
async function open(mock = mockClient()) {
  const ready = vi.fn();
  const restarted = vi.fn();
  const user = userEvent.setup();
  const mounted = render(
    <OcrReview
      client={mock as unknown as LocalOcrReviewClient}
      reviewId={REVIEW}
      resultId={REVIEW}
      onReady={ready}
      onRestarted={restarted}
    />,
  );
  const image = await screen.findByRole("img", { name: "OCR review full page 1" });
  return { mock, ready, restarted, user, image, ...mounted };
}

it("retains original observations and requires image, transcription and individual inspection", async () => {
  const { mock, ready, user, image } = await open();
  expect(screen.getByText("uncertain synthetic text")).toBeVisible();
  expect(screen.getByText("ordinary synthetic text")).toBeVisible();
  expect(screen.getByText("0.42")).toBeVisible();
  expect(screen.getByText("0.99")).toBeVisible();
  const confirm = screen.getByRole("button", { name: "Confirm this individual reading" });
  expect(confirm).toBeDisabled();
  expect(screen.getByLabelText("Exact visible reading")).toHaveValue("");
  expect(mock.confirm).not.toHaveBeenCalled();
  fireEvent.load(image);
  expect(screen.getByRole("img", { name: `OCR review region crop ${ITEM}` })).toHaveAttribute(
    "viewBox",
    "10 265 30 15",
  );
  await user.type(screen.getByLabelText("Exact visible reading"), "visible synthetic text");
  expect(confirm).toBeDisabled();
  await user.click(screen.getByRole("checkbox"));
  const updated = ocrView();
  updated.items[0]!.confirmed_reading = "visible synthetic text";
  mock.confirm.mockResolvedValue(updated);
  await user.click(confirm);
  expect(mock.confirm).toHaveBeenCalledExactlyOnceWith(ocrView(), ITEM, "visible synthetic text");
  expect(screen.getByText("0.42")).toBeVisible();
  expect(screen.getByText("uncertain synthetic text")).toBeVisible();
  expect(ready).not.toHaveBeenCalledWith(true);
  expect(screen.queryByRole("button", { name: /approve all/i })).not.toBeInTheDocument();
});

it("resets transcription for each item and requires exact placeholder transcription", async () => {
  const { mock, ready, user, image } = await open();
  fireEvent.load(image);
  await user.type(screen.getByLabelText("Exact visible reading"), "unfinished");
  await user.click(screen.getByRole("checkbox"));
  await user.selectOptions(screen.getByLabelText("Required OCR item"), PLACEHOLDER);
  expect(screen.getByLabelText("Exact visible reading")).toHaveValue("");
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  await user.type(screen.getByLabelText("Exact visible reading"), "incorrect");
  await user.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("button", { name: "Confirm this individual reading" })).toBeDisabled();
  expect(mock.confirm).not.toHaveBeenCalled();
  await user.clear(screen.getByLabelText("Exact visible reading"));
  await user.type(screen.getByLabelText("Exact visible reading"), "[[PT-SYNTHETIC]");
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  await user.click(screen.getByRole("checkbox"));
  const updated = ocrView();
  updated.items[1]!.confirmed_reading = "[PT-SYNTHETIC]";
  mock.confirm.mockResolvedValue(updated);
  await user.click(screen.getByRole("button", { name: "Confirm this individual reading" }));
  expect(mock.confirm).toHaveBeenCalledExactlyOnceWith(ocrView(), PLACEHOLDER, "[PT-SYNTHETIC]");
  expect(ready).not.toHaveBeenCalledWith(true);
});

it("locks confirmations after image failure even if a subsequent load event fires", async () => {
  const { mock, image, user } = await open();
  fireEvent.error(image);
  fireEvent.load(image);
  expect(screen.getByRole("alert")).toHaveTextContent("exact page image");
  expect(screen.getByLabelText("Exact visible reading")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Confirm this individual reading" }));
  expect(mock.confirm).not.toHaveBeenCalled();
});

it("requires reload after an uncertain confirmation and never resends it automatically", async () => {
  const { mock, user, image } = await open();
  fireEvent.load(image);
  mock.confirm.mockRejectedValue(new Error("lost reply"));
  await user.type(screen.getByLabelText("Exact visible reading"), "visible");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Confirm this individual reading" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("reconcile its current receipts");
  expect(screen.getByRole("button", { name: "Confirm this individual reading" })).toBeDisabled();
  expect(mock.confirm).toHaveBeenCalledTimes(1);
  mock.get.mockResolvedValue(completedView());
  await user.click(screen.getByRole("button", { name: "Reload local OCR review" }));
  await screen.findByText(/This stage has an exact local review receipt/);
  expect(mock.confirm).toHaveBeenCalledTimes(1);
});

it("prevents a reload from racing a pending individual confirmation", async () => {
  const { mock, user, image } = await open();
  fireEvent.load(image);
  let settle!: () => void;
  mock.confirm.mockImplementation(
    () =>
      new Promise((resolve) => {
        settle = () => resolve(completedView());
      }),
  );
  await user.type(screen.getByLabelText("Exact visible reading"), "visible");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Confirm this individual reading" }));
  expect(screen.getByRole("button", { name: "Reload local OCR review" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Confirm this individual reading" })).toBeDisabled();
  settle();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Reload local OCR review" })).toBeEnabled(),
  );
  expect(mock.confirm).toHaveBeenCalledTimes(1);
});

it("enables resume only for an exact current-stage receipt and revokes page URLs", async () => {
  const mock = mockClient();
  mock.get.mockResolvedValue(completedView());
  const { ready, unmount } = await open(mock);
  await waitFor(() => expect(ready).toHaveBeenLastCalledWith(true));
  expect(screen.getByText("Review published document OCR")).toBeVisible();
  unmount();
  expect(revoke).toHaveBeenCalledWith("blob:synthetic-ocr-page");
  const next = mockClient();
  const restored = ocrView();
  restored.stage = "restored";
  restored.receipts = completedView().receipts;
  next.get.mockResolvedValue(restored);
  const result = await open(next);
  expect(screen.getByText("Review restored candidate OCR")).toBeVisible();
  expect(result.ready).not.toHaveBeenCalledWith(true);
  expect(screen.getByLabelText("Exact visible reading")).toHaveValue("");
});

it("shows missing original scores without manufacturing a confidence", async () => {
  const mock = mockClient();
  const view = ocrView();
  view.observations[0]!.confidence = null;
  mock.get.mockResolvedValue(view);
  await open(mock);
  expect(screen.getByText("Missing")).toBeVisible();
  expect(screen.queryByText("0.42")).not.toBeInTheDocument();
  expect(mock.confirm).not.toHaveBeenCalled();
});

it("restarts only by explicit action, clears readiness and never restores automatically", async () => {
  const mock = mockClient();
  mock.get.mockResolvedValue(completedView());
  const { user, ready, restarted } = await open(mock);
  expect(mock.restart).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Restart local OCR review" }));
  expect(mock.restart).toHaveBeenCalledExactlyOnceWith(REVIEW, REVIEW);
  expect(ready).toHaveBeenLastCalledWith(false);
  expect(restarted).toHaveBeenCalledTimes(1);
  expect(mock.confirm).not.toHaveBeenCalled();
});

it("offers explicit restart after an expired review GET without trusting its old receipts", async () => {
  const mock = mockClient();
  mock.get.mockRejectedValue(new Error("expired"));
  const ready = vi.fn();
  const restarted = vi.fn();
  render(
    <OcrReview
      client={mock as unknown as LocalOcrReviewClient}
      reviewId={REVIEW}
      resultId={REVIEW}
      onReady={ready}
      onRestarted={restarted}
    />,
  );
  await screen.findByRole("alert");
  expect(ready).not.toHaveBeenCalledWith(true);
  await userEvent.setup().click(screen.getByRole("button", { name: "Restart local OCR review" }));
  expect(restarted).toHaveBeenCalledTimes(1);
  expect(mock.restart).toHaveBeenCalledTimes(1);
});

it("locks unknown restart outcomes without resending or enabling resume", async () => {
  const { mock, user, ready, restarted } = await open();
  mock.restart.mockRejectedValue(new Error("lost restart response"));
  await user.click(screen.getByRole("button", { name: "Restart local OCR review" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "restart did not return a verified result",
  );
  expect(screen.getByRole("button", { name: "Restart local OCR review" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reload local OCR review" })).toBeDisabled();
  expect(restarted).not.toHaveBeenCalled();
  expect(ready).toHaveBeenLastCalledWith(false);
  expect(mock.restart).toHaveBeenCalledTimes(1);
});
