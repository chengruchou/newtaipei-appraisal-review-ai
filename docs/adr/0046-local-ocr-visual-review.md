# ADR 0046: Exact local visual review of uncertain restoration OCR

Status: Implemented for local integration. Automated synthetic UI confirmations
are not independent human approval or an OCR accuracy acceptance claim.

## Decision

Keep the automatic restoration executor's confidence and token checks. Its
failure does not become a successful OCR measurement. An explicitly configured
local resolver may offer a separate visual-review session when real OCR is
uncertain. Unknown review authority remains rejected. The production bridge
requires a revalidated pinned OCR engine/asset identity for this path.

A session binds the authenticated principal, current publication and immutable
object version through its authority callback, original PDF, exact mapping and
plan, all replacement destinations, a unique local output path, OCR identity,
rendered page hashes and complete original OCR observations. Its lifetime is at
most 15 minutes and never exceeds mapping retention. Each stage has its own
digest and server-owned receipt. Restart loses the in-memory authority; callers
cannot import receipts, edit confidence, supply an actor or grant themselves
permission by submitting a matching hash.

The UI displays every measured observation and its original confidence. A
required observation or placeholder cell needs an individual, nonempty reading
from a viewed, hash-bound page. Placeholder cells list all their underlying raw
observations, including incorrect or low-confidence strings. A complete human
cell reading is separate evidence, never a replacement OCR observation. Missing
and unexpected token readings require explicit review; deterministic token
inventory validation still rejects a transcription of an unauthorized token.
The same validator checks automatically measured and separately reviewed text
regions. No automatic approval or approve-all endpoint exists.

Raster placeholders have no native token text. Their identity instead remains
bound by the coordinator's exact signed C2 manifest, mapping and pixel equality
between the immutable sanitized template and publication. Human review cannot
override changed pixels, native token-integrity failure, unsupported geometry,
missing OCR output, mapping failure, source/download changes or current
publication/permission failures. Native text remains an independent check.

The first receipt covers the published input. Only then may the isolated writer
prepare a private candidate in memory. The candidate receives fresh full-page
OCR and a separate review stage with its own exact input/render/observation
binding. An unresolved final stage cannot create a downloadable file. A final
receipt permits a new confined local PDF; originals and downloaded placeholders
remain byte-identical. Retrying the same completed review returns the same file
and receipts. All reads, confirmations, resumption and final downloads recheck
current authority, engine, source, retained evidence and expiration.

Expiration is checked before and after slow authority and engine reads, using
both the wall clock and a monotonic deadline. A session retains at most two
reviews, each bounded to 64 MiB of PDF, PNG and text payload, 10,000 observations
per stage and bounded transcripts. Capacity is checked before resolving or
rendering a new result. Reaching a limit refuses further work without dropping
old or failing observations.

An explicit authenticated restart may recover an expired or incorrectly read
unfinished review. It rechecks current publication, mapping, source, download
and permission, then archives every retained stage image, raw measurement,
reading and receipt in a confined private directory. Only after successful
archive verification is the old authority revoked and removed. The next restore
creates new identifiers and requires every reading again. Four archives of at
most 64 MiB are permitted per workspace; reaching that limit refuses restart.
Completed reviews cannot restart. Archives cannot authorize a download or be
imported as receipts, and no background cleanup silently erases evidence.

## Contracts and evidence

The separate loopback endpoints and DTOs are documented in
[local OCR review](../local-privacy-ocr-review.md) and generated into
`schemas/local-privacy-refill-review-v1.json`. They do not enter the central
review API, C2, prompts or cloud logs. Original and final-stage measurements and
page images remain retained in the local review session; the rehearsal also
records actual OCR observations in private local files.

Tests preserve the original automatic failure, exercise explicit synthetic
per-region confirmations and actual isolated PDF crop writing, and reject stale
stages, unknown items, unviewed/changed images, altered measurements, substituted
candidate bytes, revoked authority, changed engine/assets, changed original or
download bytes and expired sessions. The real original CJK browser rehearsal
must still be executed against the final integrated runtime before reporting
end-to-end acceptance. Genuine material approval remains an external gate.
