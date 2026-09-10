import type { ReviewSnapshot, ExportPreview } from "@/privacy/client";

export const SOURCE = "11111111-1111-4111-8111-111111111111";
export const HASH = "a".repeat(64);
export function snapshot(confirmed = false): ReviewSnapshot {
  return {
    digest: HASH,
    view: {
      schema_version: "local-privacy-v1",
      state: confirmed ? "confirmed" : "awaiting_confirmation",
      scan: null,
      events: [],
      command: {
        schema_version: "local-privacy-v1",
        source: {
          schema_version: "local-privacy-v1",
          case_id: SOURCE,
          document_id: SOURCE,
          snapshot_id: SOURCE,
          source_revision: 1,
          source_digest: HASH,
          byte_size: 100,
          pages: [
            {
              number: 1,
              width: 200,
              height: 300,
              coordinate_system: "pdf_bottom_left",
              page_space: "unrotated_crop_box",
              rotation: 0,
              crop_box: [0, 0, 200, 300],
            },
          ],
        },
        selection_revision: 1,
        policy_digest: HASH,
        reviewed_pages: confirmed ? [1] : [],
        selections: [],
      },
    },
  };
}
export const preview: ExportPreview = {
  preview_id: SOURCE,
  payload_digest: HASH,
  reviewer_text: null,
  manifest: {
    schema_version: "privacy-v1",
    processor_version: "privacy-processor-v1",
    policy_version: "privacy-policy-v1",
    case_id: SOURCE,
    document_id: SOURCE,
    sanitized_digest: HASH,
    byte_size: 100,
    filename: "sanitized.pdf",
    media_type: "application/pdf",
    pages: [
      {
        number: 1,
        width: 200,
        height: 300,
        coordinate_system: "pdf_bottom_left",
        page_space: "unrotated_crop_box",
      },
    ],
    occurrences: [],
  },
};
