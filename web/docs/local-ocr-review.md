# Local visual OCR review

The optional local bridge can return `409 local_privacy_review_required` for an
authorized restoration. `PrivacyWorkbench` then loads `OcrReview` using the
separate local session. No cloud service schema, cloud credential or cloud
request carries these pages, transcriptions or receipts.

The published document and the restored candidate have separate review stages.
The client validates the bridge view against the exported Pydantic
`OCRReviewView` schema in `src/privacy/ocr-review-contract.json`. It requires
unique page, observation and item identifiers and connected, bounded regions.
It preserves every original observation and confidence, including missing
confidence, and displays them by page. Confirmations cannot change those
measurements or silently confirm another item.

Each page is fetched with authenticated local transport, verified against its
SHA-256, and displayed using a temporary blob URL. The full page and selected
crop use the same verified bytes and PDF bottom-left coordinates. An individual
item requires a loaded page, a nonempty explicit transcription and an inspection
checkbox. Editing the reading clears the checkbox; changing items clears both.
Placeholder readings must exactly match the expected token visible in the
bound region. There is no bulk confirmation or automatic copying of OCR text.

Each confirmation carries the current `review_digest` and
`page_image_sha256`. Partial confirmation leaves Restore disabled. Only the
aggregate receipt for the current stage, exact measurements, page hashes and
individual readings enables an explicit Restore action. A published-stage
receipt cannot authorize the restored-candidate stage. Transport or validation
failure requires a read-only reload to reconcile the server state; the client
does not automatically resend a confirmation. The backend rechecks source,
plan, mapping, engine, publication and current authority on every operation.
The receipt grants local visual readings only; business authority is unchanged.

An explicit Restart control is available for expired reviews or incorrect
immutable readings, including when the current view can no longer be fetched.
It revokes the old review through the local bridge, which retains the complete
previous evidence. The response must name the same authorized result. A fresh
Restore remains a separate action; the UI never restarts or confirms readings
automatically. An unknown restart outcome locks the controls for reconciliation.

The automatic OCR confidence failures remain failures. Native-text, pixel and
security checks cannot be overridden by this screen. Final PDF bytes are
downloaded only after the bridge returns an authorized final manifest, and the
client checks the final digest as before. Disconnecting or changing stages
revokes owned page URLs.

Restoration has a bounded 120-second default response deadline because its
initial operation can render and inspect all pages. Other local operations keep
their 30-second deadline. A configured test timeout still overrides both.
Exceeding either deadline aborts the request and never retries automatically.

## Verification

`tests/ocr-review-client.test.ts`, `tests/ocr-review-ui.test.tsx` and
`tests/ocr-review-restore.test.tsx` exercise schema checks, exact image binding,
measurement preservation, individual confirmation, uncertainty recovery and
separate stage receipts using synthetic fixtures. These tests do not establish
human approval or live deployment acceptance.

`e2e-real/privacy-flow.spec.ts` remains the original full-case positive flow.
It calls the shared test-only `ocr-review-helper.ts` on the canonical
review-required response, completes both individual review stages, and retains
all original source, task, publication and downloaded-PDF assertions.
`e2e-real/ocr-review-flow.spec.ts` is a focused resume wrapper for the same
helper. Both use the actual local bridge and production browser build with no
route mocks. Configure the existing private `REVIEW_BROWSER_FIXTURE` and
`PRIVACY_BROWSER_FIXTURE` plus an ignored, local
`PRIVACY_OCR_READING_DIRECTORY`. The test saves private stage views and actual
page/crop images there and waits for explicit `published-readings.json` and
`restored-readings.json` plans prepared by inspecting those exact images.

Each plan must declare `purpose: automated_synthetic_ui_workflow`, the stage,
current `review_digest`, exact `input_sha256`, and one reading per required
`item_id`, each bound to its `page_image_sha256`. The browser enters each
reading and clicks its individual confirmation. It verifies original
observations remain unchanged, both stage receipts exist, and downloaded PDF
bytes match the final manifest. Those automated synthetic clicks prove the
workflow, not independent human approval. Keep plans, pages, raw observations,
receipts, local paths and generated PDFs out of submitted artifacts and logs.

While the full positive flow is paused before its first reading plan,
`e2e-real/ocr-review-automatic.spec.ts` independently exercises the actual HTTP
refusal. It requires the same pending case, no confirmations or receipts,
unchanged original observations, and the exact initial diagnostic request ID.
Run this API-only regression with a separate Playwright configuration that
does not start another proxy. Its successful 409 assertions are distinct from
the positive flow's required final successful restoration. Preliminary failed
baseline logs remain historical failure evidence.

To refresh the local schema from the existing declared Python environment,
serialize `OCRReviewView.model_json_schema(mode="serialization")` from
`appraisal_review.domain.privacy_refill_review`, then format only
`src/privacy/ocr-review-contract.json`. This local export is independent of the
central OpenAPI generator. The durable backend decision is ADR 0046.
