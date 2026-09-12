import type { OcrReviewView } from "@/privacy/ocr-review-client";

export const REVIEW = "10000000-0000-4000-8000-000000000001";
export const OBS = "10000000-0000-4000-8000-000000000002";
export const ITEM = "10000000-0000-4000-8000-000000000003";
export const PLACEHOLDER = "10000000-0000-4000-8000-000000000004";
export const HASH = "a".repeat(64);
export function ocrView(): OcrReviewView {
  return {
    review_id: REVIEW,
    stage: "published",
    review_digest: HASH,
    engine_digest: HASH,
    input_sha256: HASH,
    pages: [{ number: 1, width: 200, height: 300, image_sha256: HASH }],
    observations: [
      {
        observation_id: OBS,
        page: 1,
        bbox: [10, 20, 40, 35],
        raw_text: "uncertain synthetic text",
        confidence: 0.42,
        review_item_id: ITEM,
      },
      {
        observation_id: "10000000-0000-4000-8000-000000000005",
        page: 1,
        bbox: [10, 100, 30, 115],
        raw_text: "ordinary synthetic text",
        confidence: 0.99,
        review_item_id: null,
      },
    ],
    items: [
      {
        item_id: ITEM,
        kind: "observation",
        page: 1,
        bbox: [10, 20, 40, 35],
        observation_ids: [OBS],
        expected_text: null,
        confirmed_reading: null,
      },
      {
        item_id: PLACEHOLDER,
        kind: "placeholder",
        page: 1,
        bbox: [10, 50, 90, 65],
        observation_ids: [],
        expected_text: "[PT-SYNTHETIC]",
        confirmed_reading: null,
      },
    ],
    receipts: [],
  };
}

export function completedView(stage: OcrReviewView["stage"] = "published"): OcrReviewView {
  const view = ocrView();
  view.stage = stage;
  for (const item of view.items)
    item.confirmed_reading = item.expected_text ?? "visible synthetic text";
  view.receipts = [
    {
      receipt_id: "10000000-0000-4000-8000-000000000006",
      review_id: REVIEW,
      stage,
      principal_id: "synthetic-local-reviewer",
      binding_digest: HASH,
      review_digest: HASH,
      engine_digest: HASH,
      input_sha256: HASH,
      measurements_digest: HASH,
      page_image_sha256: [HASH],
      readings: structuredClone(view.items),
      confirmed_at: "2026-01-01T00:00:00Z",
      expires_at: "2099-01-01T00:00:00Z",
      authority_scope: "local_visual_ocr_readings_only",
      business_authority: "unchanged",
    },
  ];
  return view;
}
