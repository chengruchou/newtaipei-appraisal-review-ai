# Local OCR review for exact PDF restoration

The existing automatic restoration path still refuses low-confidence OCR and
invalid placeholder inventory. A configured `LocalRestoreCoordinator` may
supply `ocr_identity`, a trusted callback that revalidates the actual pinned
local engine and assets and returns their digest. Without this callback the
bridge offers no visual-review authority. The combined local rehearsal uses
`TesseractPrivacyOutputOCR.identity`; no model is called remotely.

The bridge exposes these session-authenticated loopback routes:

| Route | Result |
| --- | --- |
| `POST /local-privacy/restore/{result_id}` | Existing success, or HTTP 409 with `code=local_privacy_review_required` and `review_id` |
| `GET /local-privacy/restore-reviews/{review_id}` | Current published/restored stage, digest, page hashes, all raw measurements, required review items and receipts |
| `GET /local-privacy/restore-reviews/{review_id}/pages/{page}` | Actual rendered PNG; records that this exact page was retrieved |
| `POST /local-privacy/restore-reviews/{review_id}/items/{item_id}/confirm` | Confirms one reading against `review_digest` and `page_image_sha256`; returns the current view |
| `GET /local-privacy/restore-reviews/{review_id}/stages/published` | Retained published-stage measurements and review state |
| `POST /local-privacy/restore-reviews/{review_id}/restart` | Empty command archives unfinished review evidence, revokes old confirmations and returns `state=restarted` with the same `result_id` |
| `GET /local-privacy/restored/{local_id}` | Final authorized PDF after both stage requirements have passed |

A bbox is `[x0, y0, x1, y1]` in PDF bottom-left coordinates. Page dimensions use
the same point space. `observations` preserve the complete original text,
confidence and geometry. `items` list only the required individual reviews.
A placeholder item references all contained observation IDs and the exact token
that the reviewer must visually transcribe. A missing token cell remains an
explicit item with no invented OCR text or score. Normal observation items
require the actual visible reading; confidence cannot be edited.

Confirmation requires a viewed page, exact current-stage hashes and a nonempty
reading. No client-supplied identity, generic approval, uploaded receipt or
bulk-approval command is accepted. Confirmed readings are immutable. A stage
receipt is created only after all required readings are confirmed and the
resulting token inventory validates. Receipts bind principal, full session scope,
input, engine, all measurements, all page hashes, exact readings and expiry.
The `business_authority` field remains `unchanged`.

The original restore command resumes after input review. It may return another
409 for the final candidate stage; the candidate PDF remains private in memory.
After final-stage review, success also returns `ocr_review_receipts` alongside
the existing local ID and manifest. Repeating this command reuses the same final
file. Permission, publication, mapping, engine, original/download bytes, receipt
or evidence changes block subsequent access. In-memory receipts cannot survive
process restart or be imported as authorization.

An expired or incorrectly transcribed unfinished review requires the explicit
restart command followed by a separate restore command. Restart revalidates
current source, publication, mapping, download and permissions, including when
the old receipt has expired. Every old stage image, raw observation, reading and
receipt is archived privately before invalidation. Fresh identifiers and all
new confirmations are required. A completed review cannot restart.

Each session retains at most two reviews of 64 MiB each, with at most 10,000
observations per stage. The workspace retains at most four private restart
archives of 64 MiB each. Full capacity refuses new work; evidence is never
silently discarded. A lease lasts at most 15 minutes, bounded by mapping expiry,
and is checked again after slow trust checks as well as before them.

The local schema is generated with:

```bash
python scripts/export_privacy_refill_review_contracts.py
```

This schema is separate from the central/cloud OpenAPI document. The review
route is a local display transformation; it does not approve real materials,
competition upload, model quality, business policy or deployment. Any automated
synthetic UI confirmations must be identified as such in validation reports.
See [ADR 0046](adr/0046-local-ocr-visual-review.md) for the trust boundary.
