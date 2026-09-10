# ADR 0022: Bounded local acquisition and explicit scan coverage

Status: proposed, Phase 2 local working implementation, 2026-09-10.
Builds on [ADR 0021](0021-local-privacy-boundary.md); Linux remains the runtime
acceptance target. Windows observations do not certify Linux execution.

## Context

The legacy PDF parser has useful geometry and extraction behavior but reloads
paths and uses a shared process pool without a per-request kill deadline.
Privacy inspection needs fixed original bytes and honest coverage of scanned or
mixed pages. Native text alone cannot prove an image page has been inspected.
No approved local OCR engine/model set was available during development.

## Decision

### Acquisition and source ownership

The new local adapter opens only relative selections beneath an explicitly
configured, operator-owned root. Linux traversal uses directory descriptors and
`O_NOFOLLOW` for each component, `O_DIRECTORY` for intermediate directories and
`O_NONBLOCK` for the leaf. Absolute paths, alternate separators, parent traversal,
symlinks, nonregular leaves, hardlinks and oversized files are rejected. The root
and its ancestors must be under the trusted operator's control; this is not an
authorization service for hostile local users.

Check file descriptor identity, length and modification metadata before/after
reading, then own the resulting immutable bytes. Each capture gets fresh random
document/snapshot IDs. Scanning and rendering read the owned bytes, never reopen
the original path. A later path replacement cannot change an existing snapshot.
The source-file path is retained only in the local store as capture provenance;
it is not a claim that the current file at that path still equals the snapshot.
The store checks exact snapshot metadata and hash on access. It is ephemeral,
limited to eight snapshots and a configured original-byte budget, not a durable
or encrypted mapping store. `forget` releases references without claiming secure
erasure. Concurrent service-level admission/quotas are future application work.

The non-Linux fallback is for development. It checks reparse points/symlinks and
descriptor metadata, but makes no Windows ACL or equivalent race-resistance
claim. It does not weaken the existing Linux/macOS reviewer authority gate.

### PDF and subprocess limits

Keep PDF calls in a dedicated interpreter, one request per process. The new
adapter follows the legacy parser's process-isolation and CropBox conventions
without changing that parser or its existing consumers. It does not reuse the
shared pool because timeout must terminate the actual work, not only stop waiting.

Private stdin carries original bytes in a bounded JSON/base64 envelope. Only
captured stdout returns local evidence/raster data; stderr is discarded, never
copied to reports. No source text or original filename appears in process argv.
The generic runner uses no shell, limits output and wall time, and kills its
owned Linux process group on timeout/overflow. Pipe helper threads never call
PyMuPDF. The interpreter uses isolated mode and does not inherit credentials,
Python path overrides or telemetry settings. Linux workers additionally bound
address space and CPU time. These controls are not a network or syscall sandbox
and do not claim protection from native-code exploits or a compromised operator.

Default PDF limits: 20 MB original bytes, 100 pages, 16 million rendered pixels
per page at 144 DPI, 100,000 extracted characters per page, 1,000 candidates per
page, 30 seconds per worker/scan and 768 MiB worker address space. Parent pipe
output and snapshot retention also have finite limits. Trusted local filesystem
read latency and native Windows memory limits are not equivalent to a Linux
resource sandbox. Reject encrypted, repaired/corrupt, empty and unsupported PDFs;
do not silently accept repaired input or clip invalid evidence geometry.

Region coordinates remain one-based, unrotated CropBox-local bottom-left points.
Original `crop_box` metadata uses PyMuPDF's unrotated top-left convention, as in
the legacy parser; rotation is recorded separately. Native blocks and rendered
OCR use the same unrotated page. Pixel conversion uses actual raster dimensions,
including rounding, instead of guessing from nominal DPI. Unsupported page units
or geometry fail validation. Source bytes are never saved by the PDF worker.

Native inspection caps the document at 10,000 text observations; OCR and candidate
detection also bound observation counts. Detector matching checks the remaining
scan deadline. Empty labels do not create candidates for missing values.

### OCR and local candidate coverage

Tesseract is an optional local adapter with explicit executable and traineddata
paths and expected SHA-256 hashes. It never downloads models or chooses URLs.
Preflight checks assets and a bounded version invocation. Recognition also checks
preflight, then sends only a locally rendered PNG to stdin and requests TSV on
stdout, with a fixed command and configured local language data. This application
requires both `chi_tra` and `eng` for image-page capability; English-only assets
cannot claim coverage of Traditional Chinese case documents.

Native text blocks retain unknown confidence. OCR words retain raw recognized
text and the engine's word score divided by 100; invalid or negative scores fail,
and scores are never raised. Original OCR observations, engine version and model
digests remain in local coverage records. Confidence below 0.85 is a review issue,
not a statement about business fact reliability or an approval threshold.

Pages with any detected image or no native text require a full rendered OCR pass.
Native and OCR observations are both retained on mixed pages. Missing OCR,
missing languages, hash mismatch, timeout, empty OCR and malformed coordinates
block the scan; the report still accounts for every page. No remote fallback
exists. The old complete-only detector protocol raises on blocked reports.

Fixed format and label patterns propose candidates for identifiers, phone,
email, names, addresses, birth dates, accounts, parcels and ownership/contact
labels. These are conservative local heuristics, not NER or universal sensitive
data detection. Regex matches do not validate an identity-number checksum or
establish that a number belongs to a real person. A candidate covers all source
blocks/words contributing to a match, which may be wider than the exact glyphs.
Cross-observation detection uses documented newline separators; original word
observations remain separately available. Candidate confidence is the minimum
contributing OCR score or unknown if any contributing native score is unknown.

Every page requires manual review of all sensitive categories, including
unlabeled names/addresses, faces, stamps and signatures. No automatic grouping or
image classifier is claimed. Zero matches never means safe. A fully processed
scan is `needs_review`, never `confirmed`, `verified` or `exportable`.

### Contracts and next boundaries

Add `local-privacy-scan-v1` for local observations, per-page coverage and capability
reports. Keep Phase 1 public and local schemas and service-v1 unchanged.
The new scan report is evidence, not authority; Phase 3 must bind the trusted
stored scan and complete review selections before issuing human confirmation.
Sanitization, hidden-object removal, immutable export, mapping encryption and
refill remain later phases. Do not use the scanner as an upload gate.

## Evidence and limitations

Tests exercise real generated PDFs, independent worker processes, geometry,
immutable snapshot behavior and refusal paths. OCR TSV/transport tests use
explicit doubles; they do not establish real recognition accuracy. Linux
openat/symlink/FIFO coverage is explicitly Linux-only and remains unexecuted on
the Windows development host. No existing gate or test was weakened.

The synthetic smoke command exits nonzero when any scan is blocked or its OCR
canaries are not recognized. It reports counts, fixed issues and unchanged-byte
booleans without source text/hashes. Actual OCR and Linux execution remain
acceptance prerequisites, not inferred from a successful mocked test.

Implementation references checked during development:
[PyMuPDF process guidance](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html),
[page API](https://pymupdf.readthedocs.io/en/latest/page.html),
[Tesseract command usage](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).
No new Python dependency, system engine, model, font or keystore was installed.
