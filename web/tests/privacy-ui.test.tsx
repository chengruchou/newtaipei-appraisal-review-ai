import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LocalPrivacyClient, type ReviewSnapshot } from "@/privacy/client";
import { PrivacyReviewPanel } from "@/privacy/PrivacyWorkbench";

import { SOURCE, snapshot, preview } from "./privacy-fixtures";

function client(confirmed = true) {
  return {
    sources: vi.fn().mockResolvedValue([SOURCE]),
    open: vi.fn().mockResolvedValue(snapshot(confirmed)),
    page: vi.fn().mockResolvedValue(new Blob(["png"])),
    reload: vi.fn().mockResolvedValue(snapshot()),
    markPage: vi.fn().mockResolvedValue({
      ...snapshot(),
      digest: "b".repeat(64),
      view: {
        ...snapshot().view,
        command: { ...snapshot().view.command, reviewed_pages: [1], selection_revision: 2 },
      },
    }),
    confirmReview: vi.fn().mockResolvedValue(snapshot(true)),
    prepare: vi.fn().mockResolvedValue(preview),
    previewPdf: vi.fn().mockResolvedValue(new Blob(["pdf"])),
    confirmExport: vi.fn().mockResolvedValue(undefined),
    transfer: vi.fn().mockResolvedValue(undefined),
    add: vi.fn().mockResolvedValue(snapshot()),
    edit: vi.fn().mockResolvedValue(snapshot()),
    remove: vi.fn().mockResolvedValue(snapshot()),
    restore: vi.fn().mockResolvedValue({ local_id: SOURCE, manifest: {} }),
    restoredPdf: vi.fn().mockResolvedValue(new Blob(["restored"])),
  };
}
beforeEach(() =>
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:local-preview"),
    revokeObjectURL: vi.fn(),
  }),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
async function open(mock: ReturnType<typeof client>) {
  const user = userEvent.setup();
  render(<PrivacyReviewPanel client={mock as unknown as LocalPrivacyClient} />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Open selected source" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "Open selected source" }));
  return user;
}

it("requires the original PNG to load before explicit page review", async () => {
  const mock = client(false);
  const user = await open(mock);
  const mark = screen.getByRole("button", { name: "I reviewed this page and its marked regions" });
  expect(mark).toBeDisabled();
  expect(mock.markPage).not.toHaveBeenCalled();
  fireEvent.load(await screen.findByRole("img", { name: "Original source page 1" }));
  await user.click(mark);
  expect(mock.markPage).toHaveBeenCalledWith(
    { schema_version: "local-privacy-v1", case_id: SOURCE, snapshot_id: SOURCE, revision: 1 },
    1,
  );
  expect(mock.confirmReview).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Confirm reviewed source and regions" }));
  const reviewed = mock.confirmReview.mock.calls[0]?.[0] as ReviewSnapshot;
  expect(reviewed.digest).toBe("b".repeat(64));
  expect(reviewed.view.command.selection_revision).toBe(2);
});

it("requires actual preview load and explicit exact-payload confirmation before transfer", async () => {
  const mock = client();
  const user = await open(mock);
  await user.click(screen.getByRole("button", { name: "Prepare exact sanitized preview" }));
  const confirm = screen.getByRole("button", { name: "Confirm this exact sanitized payload" });
  expect(confirm).toBeDisabled();
  expect(mock.confirmExport).not.toHaveBeenCalled();
  fireEvent.load(screen.getByTitle("Sanitized PDF preview"));
  await user.click(screen.getByRole("checkbox"));
  await user.click(confirm);
  expect(mock.confirmExport).toHaveBeenCalledWith(preview);
  expect(mock.transfer).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Transfer confirmed payload once" }));
  expect(mock.transfer).toHaveBeenCalledTimes(1);
  expect(mock.transfer).toHaveBeenCalledWith(SOURCE);
});

it("does not retry or allow a new export after an unknown transfer outcome", async () => {
  const mock = client();
  mock.transfer.mockRejectedValue(new Error("connection lost"));
  const user = await open(mock);
  await user.click(screen.getByRole("button", { name: "Prepare exact sanitized preview" }));
  fireEvent.load(screen.getByTitle("Sanitized PDF preview"));
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Confirm this exact sanitized payload" }));
  await user.click(screen.getByRole("button", { name: "Transfer confirmed payload once" }));
  expect(screen.getByText(/Do not repeat it or create another export/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Transfer confirmed payload once" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Prepare exact sanitized preview" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Open selected source" })).toBeDisabled();
  expect(mock.transfer).toHaveBeenCalledTimes(1);
});

it("invalidates the exact preview when a source region changes", async () => {
  const mock = client();
  const user = await open(mock);
  await user.click(screen.getByRole("button", { name: "Prepare exact sanitized preview" }));
  for (const [label, value] of [
    ["Left x", "10"],
    ["Bottom y", "20"],
    ["Right x", "40"],
    ["Top y", "50"],
  ])
    await user.type(screen.getByLabelText(label!), value!);
  await user.click(screen.getByRole("button", { name: "Add redaction region" }));
  expect(screen.queryByTitle("Sanitized PDF preview")).not.toBeInTheDocument();
  expect(mock.confirmExport).not.toHaveBeenCalled();
  expect(mock.transfer).not.toHaveBeenCalled();
});

it("submits a real page-bounded region correction and an opaque restore identifier", async () => {
  const mock = client();
  const user = await open(mock);
  for (const [label, value] of [
    ["Left x", "10"],
    ["Bottom y", "20"],
    ["Right x", "40"],
    ["Top y", "50"],
  ])
    await user.type(screen.getByLabelText(label!), value!);
  await user.click(screen.getByRole("button", { name: "Add redaction region" }));
  expect(mock.add).toHaveBeenCalledWith({
    schema_version: "local-privacy-v1",
    case_id: SOURCE,
    snapshot_id: SOURCE,
    revision: 1,
    region: { page: 1, bbox: [10, 20, 40, 50] },
    category: "name",
  });
  await user.type(screen.getByLabelText("Authorized result identifier"), SOURCE);
  await user.click(screen.getByRole("button", { name: "Restore result through local bridge" }));
  expect(mock.restore).toHaveBeenCalledTimes(1);
  expect(mock.restore).toHaveBeenCalledWith(SOURCE);
  expect(await screen.findByRole("link", { name: "Save restored PDF locally" })).toHaveAttribute(
    "href",
    "blob:local-preview",
  );
});
