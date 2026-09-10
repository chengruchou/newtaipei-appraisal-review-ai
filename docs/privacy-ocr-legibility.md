# Local privacy rehearsal OCR legibility

The authored privacy forms use 16-point source facts, a plain `SYNTHETIC SOURCE`
footer, and 14-point black review annotations. The synthetic name canaries,
eight reserved writer boxes, original-source preservation, and candidate
confidence remain unchanged. Source identity comes from the captured document
hash and configured identifiers; the former decorative UUID footer was not an
identity credential.

`create_privacy_rehearsal(..., raster_dpi=144)` accepts the existing `ScanLimits`
DPI range (72 through 300). It validates the value before creating a workspace
and supplies it to the real isolated sanitizer for the initial export. This is
separate from the configured restoration renderer/OCR DPI. Increasing only the
latter cannot recover detail lost in an earlier rasterization. Changed fixture
assets or initial export settings require a fresh private namespace, fresh
review, and explicit confirmation of the newly built exact payload.

## Verification boundary

Restoration still requires every actual OCR observation to have confidence at
least 0.85 and every expected placeholder occurrence to match. No observation
is discarded, no recognized word is substituted, and no score is overridden.
The production placeholder format, font, token geometry, and restoration gate
are unchanged by these fixture adjustments. Authored extraction and export OCR
remain explicitly synthetic adapters; they do not establish measured OCR
accuracy.

The real combined HTTP run with the revised fixture completed sanitized C2
admission, four explicit review responses, publication, and construction of the
exact authorized mapping plan. Actual restoration remained rejected before
writing. This is a negative acceptance result, not a completed privacy flow.

## Diagnostic findings

The earlier actual publication had low-confidence observations for its random
UUID footer, long `PT_` hexadecimal tokens, the red Chinese inferior grade, and
one title digit. At 144 DPI, ordinary source facts and English field labels
passed. The revised source at initial 288 DPI and verification 144 DPI removed
the footer problem and retained readable source facts; grade annotations and
random placeholder recognition still failed the strict gate.

Bounded tests preserved all words while comparing genuine page segmentation,
pinned standard and best language assets, language order, dictionary loading,
font families, resolution, and text contrast. Sparse/single-block segmentation,
Courier, alternative monospace fonts, and disabled dictionary loading did not
resolve the failure. A larger Helvetica sample improved literal recognition
without making every confidence score acceptable, so no production font change
was adopted. Stroke changes that omitted or merged tokens were rejected even
when their remaining observations had high confidence.

Independent authored CJK samples showed that a Chinese review label improved
grade recognition, but punctuation adjoining numeric percentages still produced
low-confidence observations. Those samples are diagnostic evidence only; they
have not established successful restoration of a published case.

Focused regressions check that all required authored facts and canaries remain,
and that all eight actual written regions contain visible neutral black ink
with both Chinese grades intact. Existing C2 identity, image-only evidence,
confirmation, field placement, template preservation, and publication tests
continue to apply. Detailed renderings, per-word observations, unsuccessful
trials, and logs are confined to ignored local artifacts.
