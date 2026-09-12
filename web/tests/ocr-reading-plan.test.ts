import { expect, it } from "vitest";
import { validateReadingPlan } from "../e2e-real/ocr-reading-plan";
import { ocrView } from "./ocr-review-fixtures";

function fixture() {
  const view = ocrView();
  const plan = {
    purpose: "automated_synthetic_ui_workflow",
    stage: view.stage,
    review_digest: view.review_digest,
    input_sha256: view.input_sha256,
    readings: view.items.map((item) => ({
      item_id: item.item_id,
      page_image_sha256: view.pages[0]!.image_sha256,
      reading: item.expected_text ?? "explicit visible synthetic reading",
    })),
  };
  return { view, plan };
}
it("accepts exact individual readings without changing any original observations", () => {
  const { view, plan } = fixture();
  const original = structuredClone(view);
  expect(validateReadingPlan(plan, view)).toEqual(plan);
  expect(view).toEqual(original);
});
it.each(["stage", "review_digest", "input_sha256", "purpose"] as const)(
  "rejects a mismatched %s",
  (field) => {
    const { view, plan } = fixture();
    expect(() => validateReadingPlan({ ...plan, [field]: "different" }, view)).toThrow(
      "exact captured stage",
    );
  },
);
it.each(["missing", "extra", "duplicate", "wrong-page", "blank", "control", "too-long"])(
  "rejects %s individual readings",
  (kind) => {
    const { view, plan } = fixture();
    if (kind === "missing") plan.readings = [];
    if (kind === "extra" || kind === "duplicate") plan.readings.push({ ...plan.readings[0]! });
    if (kind === "wrong-page") plan.readings[0]!.page_image_sha256 = "0".repeat(64);
    if (kind === "blank") plan.readings[0]!.reading = " ";
    if (kind === "control") plan.readings[0]!.reading = "visible\ntext";
    if (kind === "too-long") plan.readings[0]!.reading = "x".repeat(4097);
    expect(() => validateReadingPlan(plan, view)).toThrow("exact captured stage");
  },
);
it("rejects a changed placeholder transcription", () => {
  const { view, plan } = fixture();
  view.items[0]!.kind = "placeholder";
  view.items[0]!.expected_text = "exact visible placeholder";
  expect(() => validateReadingPlan(plan, view)).toThrow("exact captured stage");
});
it("rejects repeated identifiers even when the required item count matches", () => {
  const { view, plan } = fixture();
  view.items.push({ ...view.items[0]!, item_id: "second-required-item" });
  plan.readings.push({ ...plan.readings[0]! });
  expect(plan.readings).toHaveLength(view.items.length);
  expect(() => validateReadingPlan(plan, view)).toThrow("exact captured stage");
});
it("rejects an unknown item with the right number of readings", () => {
  const { view, plan } = fixture();
  plan.readings[0]!.item_id = "unknown-item";
  expect(() => validateReadingPlan(plan, view)).toThrow("exact captured stage");
});
it("cannot replace an already confirmed individual reading", () => {
  const { view, plan } = fixture();
  view.items[0]!.confirmed_reading = "previous immutable reading";
  expect(() => validateReadingPlan(plan, view)).toThrow("exact captured stage");
});
it("rejects unknown fields without echoing their values", () => {
  const { view, plan } = fixture();
  const canary = "private synthetic field canary";
  let error: unknown;
  try {
    validateReadingPlan({ ...plan, note: canary }, view);
  } catch (caught) {
    error = caught;
  }
  expect(error).toBeInstanceOf(Error);
  expect((error as Error).message).not.toContain(canary);
  expect((error as Error).cause).toBeUndefined();
});
