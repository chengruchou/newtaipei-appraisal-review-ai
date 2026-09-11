import type { OcrReviewView } from "../src/privacy/ocr-review-client";

export interface ReadingPlan {
  purpose: "automated_synthetic_ui_workflow";
  stage: OcrReviewView["stage"];
  review_digest: string;
  input_sha256: string;
  readings: { item_id: string; page_image_sha256: string; reading: string }[];
}
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, expected: string[]) {
  return Object.keys(value).sort().join("|") === expected.sort().join("|");
}
function invalid(): never {
  throw new Error("The explicit reading plan does not match this exact captured stage.");
}

export function validateReadingPlan(value: unknown, view: OcrReviewView): ReadingPlan {
  if (
    !record(value) ||
    !keys(value, ["purpose", "stage", "review_digest", "input_sha256", "readings"]) ||
    value.purpose !== "automated_synthetic_ui_workflow" ||
    value.stage !== view.stage ||
    value.review_digest !== view.review_digest ||
    value.input_sha256 !== view.input_sha256 ||
    !Array.isArray(value.readings) ||
    value.readings.length !== view.items.length
  )
    invalid();
  const seen = new Set<string>();
  for (const entry of value.readings) {
    if (
      !record(entry) ||
      !keys(entry, ["item_id", "page_image_sha256", "reading"]) ||
      typeof entry.item_id !== "string" ||
      seen.has(entry.item_id) ||
      typeof entry.reading !== "string" ||
      !entry.reading.trim() ||
      entry.reading.length > 4096 ||
      Array.from(entry.reading).some(
        (character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
      )
    )
      invalid();
    const item = view.items.find((candidate) => candidate.item_id === entry.item_id);
    const page = item && view.pages.find((candidate) => candidate.number === item.page);
    if (
      !item ||
      !page ||
      entry.page_image_sha256 !== page.image_sha256 ||
      (item.kind === "placeholder" && entry.reading !== item.expected_text) ||
      (item.confirmed_reading !== null && entry.reading !== item.confirmed_reading)
    )
      invalid();
    seen.add(entry.item_id);
  }
  return value as unknown as ReadingPlan;
}
