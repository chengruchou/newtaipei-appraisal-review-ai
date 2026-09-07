# ADR 0011: Conservative PDF mutation, fonts and publication

- Status: Proposed for human review
- Date: 2026-09-06
- Delivery: #5

## Context

PDF pages may contain translated CropBoxes, viewer rotations, nested resources,
arbitrary text operators and non-text drawing operations. A completed-form writer
must preserve the supplied template and must not claim correction when stale text
is merely hidden. Publication must also avoid exposing an unverified or racing
partial artifact. Chinese output requires a font whose provenance and glyph
coverage are explicit.

## Decision

The local writer preflights every field before mutation and operates on a scoped
temporary copy. `fill_blank`, `annotate` and `correct` remain distinct operations.
Correction supports only complete, directly measurable `Tj` and `TJ` page text
operations contained by the configured rectangle. Partial intersections, quote
text operators, undecodable fonts, ambiguous transforms and text in Form XObjects
fail. The writer removes the stale text-show operation itself and never uses a
white rectangle or replacement overlay as evidence of correction. Path, image and
unrelated text operations are preserved.

All text uses one explicitly configured absolute TrueType font path. There is no
system-font discovery or fallback. Preflight proves glyph coverage and measured
fit; mutation embeds the font, and reopen verification checks the resulting
artifact. The repository does not distribute a production CJK font. Automated
acceptance instead creates an original minimal TrueType font inside the test
temporary directory and proves one embedded CJK glyph can be extracted. Production
must supply and review a suitably licensed font independently.

The writer records source filesystem identity and SHA-256, writes in the
destination directory, reopens and validates the complete temporary PDF, and only
then publishes. Local destinations reject overwrite by default using atomic
hard-link creation. Explicit overwrite uses atomic `os.replace`. Both modes recheck
the source immediately before publication. S3 uses an injected client and uploads
only a locally validated artifact; default upload uses `If-None-Match: *`, while
explicit overwrite omits that condition.

## Consequences

- Unsupported PDFs fail rather than produce a plausible but unproven correction.
- Correction coverage is intentionally narrower than general PDF text extraction.
- Reference-only map pages are structurally compared after reopening and cannot be
  rendering targets.
- A layout overflow, missing glyph, mutation error or verification error publishes
  no new destination.
- Local publication has atomic destination visibility on the destination
  filesystem. S3 has conditional object creation but no multi-object transaction;
  mocked transfer tests are not live cloud-atomicity evidence.
- Enabling overwrite or selecting a production font is an explicit deployment
  decision, not inferred from an existing file or host configuration.

## Rejected alternatives

- Painting over old text, because the stale value remains in the PDF.
- Shrinking, wrapping or truncating values silently, because layout meaning would
  become configuration-dependent.
- Searching installed system fonts, because output would vary by host and font
  licensing would be unclear.
- Checking destination existence before an unconditional write, because another
  writer could win the race between those operations.
