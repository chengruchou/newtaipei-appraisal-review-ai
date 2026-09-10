# ADR 0035: Raster rebuild with independent privacy verification

Status: Accepted for the local Phase 4 implementation. Actual OCR and Linux
runtime acceptance remain pending.

## Context

An opaque rectangle over PDF text does not remove its underlying data. Source
documents may also contain attachments, annotations, forms, hidden layers,
metadata and incremental revisions. Issue 22 requires a clean PDF and independent
verification without harming numbers and tables outside approved regions.

## Decision

The local processor renders the owned original at configured DPI, unrotated and
relative to the original CropBox. It overwrites the approved rectangle pixels
with white using outward integer rounding, then writes a fresh image-only PDF.
No original PDF objects, font/text layers, metadata or filenames enter the writer.
An explicit canonical profile uses pypdf to encode one lossless RGB image per page.
The PDF contains only new page/image/content objects, with no source object copy,
optional-content groups, form fields, actions, annotations or attachments.

Full `PT_<random entity UUID>` placeholders occupy a new 220-point right column.
They are rasterized at 9 points, one row per occurrence, in 20-point rows. The
service checks width and available rows; overflow is an error, never permission
to shrink labels indefinitely or cover original values. The original raster is
copied at the same pixel scale. Rounded raster dimensions may extend source page
edges by less than one output pixel. Output page dimensions and occurrence boxes
in the public manifest describe this actual sanitized layout.

The verifier runs in a separate bounded request/process from generation. It:

1. Validates owned source bytes and transforms, current command, manifest lineage,
   occurrence entity/order/placement and output byte size/digest.
2. Parses the artifact with pypdf and extracts its RGB image streams. Exact
   canonical reserialization must reproduce all artifact bytes. This rejects
   extra or unreachable objects, metadata, hidden content, alternate streams,
   attachments, forms, actions, incremental history and trailing payloads.
3. Independently re-renders the original and checks every decoded output pixel.
   Approved areas must be white; every pixel outside them must equal the original
   raster. It does not call the builder's pixel-masking/copy routine. The new
   column must equal the expected full-token rendering.
4. Reopens and re-renders the result with MuPDF and compares the rendered pixels
   with the decoded RGB streams. It returns those exact page images for local OCR.
5. Requires configured local English and Traditional Chinese OCR assets. Every
   page must yield valid, sufficiently confident observations, every expected
   placeholder must be readable, and known approved source-text canaries must
   be absent after Unicode/whitespace normalization.

The canonical encoder is a shared format specification, and MuPDF renders both
source and output; this is not independence from all common library defects.
Pixel verification uses a distinct row-byte algorithm, pypdf independently parses
the PDF, and OCR provides another check. There is no claim that OCR proves the
absence of all possible PII. Correct review regions and the trusted source/scan
are still prerequisites; signatures/faces and missed detections require the
Phase 3 whole-page human review.

`LocalSanitizedBundleBuilder` checks live human authority before work and again
after independent verification. `LocalSanitizedVerifier` owns one in-memory
verified record; caller-supplied digests, manifests or bundle DTOs confer no
authority. Failed verification clears that record. Only the builder returns
the verified PDF bytes plus public manifest, with fixed filename `sanitized.pdf`.
Direct raster processor output is an unverified draft, even though it uses the
same bytes/manifest carrier type.

## Consequences

All transformations and OCR run locally. PDF processing retains the existing
bounded stdin/stdout subprocess, timeout, output cap and Linux memory/CPU limits.
Additional aggregate RGB memory and token-column capacity checks fail closed.
The output has no selectable text layer; downstream understanding must consume
sanitized pixels. No raw excerpt, source hash or approval record is in the bundle.

OCR is mandatory for verified output. Missing assets, empty/low-confidence OCR,
unreadable placeholders, remaining canaries and every structural/pixel mismatch
reject verification. Synthetic OCR doubles establish application behavior only;
they do not satisfy real-engine acceptance. Tiny text can therefore block output
despite structurally correct redaction.

This phase adds no upload API, encrypted mapping, refill or business completion.
Returning an immutable bundle is not an atomic export grant: the future Phase 7
exporter must recheck current human authority and send those exact verified bytes
atomically. Existing upload endpoints remain unsuitable for sensitive cases.
The original file is never modified. The operator account and composition remain
trusted, and OS network denial is still a separate acceptance item.
