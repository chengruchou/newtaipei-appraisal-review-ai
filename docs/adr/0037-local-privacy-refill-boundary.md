# ADR 0037: Local refill and publisher authority

Status: Accepted for the local Phase 6 implementation; production integration
and Linux runtime acceptance remain pending.

## Context

A sanitized result can contain cloud corrections and repeated placeholders for
the same entity. A supplied PDF digest does not prove its publisher, current
revision or approval. Replacing every matching string can restore sensitive data
to an unauthorized destination or overwrite a valid correction.

## Decision

Use separate trusted ports for authenticated current publication and explicit
rehydration-plan approval. Validate the encrypted mapping's case, document,
mapping identity and expiry against the plan, published run/revision, template,
base sanitized digest and actual downloaded bytes. Repeat authority and source
checks after processing. No production publisher adapter is introduced here.

Require every mapping occurrence to have a unique published output field and
one approved operation. Destinations must equal the published field region;
overlaps, unknown occurrences and implicit omission fail. An absent placeholder
requires an explicit approved omission. OCR checks every page before and after
refill, with an additional independent native-text inventory.

Rebuild the cloud artifact as a clean raster PDF, clear approved fields and
restore local text or approved original crops deterministically. Preserve pixels
outside those fields at the configured rendering resolution. Require a pinned
embedded CJK font, supported glyphs and sufficient field space. Reject overflow.
Original crops represent visual content, including visual signatures; they do
not create or preserve a cryptographic digital signature.

Publish only a new local file through the existing atomic writer utility, with
overwrite disabled and original-source identity and digest checks. The final
manifest records `refilled_local`, `unchanged` business authority and visual-only
signature semantics. Refill never completes a business case or authorizes upload.

## Consequences

The current writer supports unrotated published pages with an origin-based
CropBox. Raster rebuilding removes old PDF objects and selectable cloud text;
visual preservation is measured at the configured DPI, not every zoom level.
Unsupported geometry, missing font assets, incomplete OCR tokens or changed
authority fail explicitly. The local final PDF contains sensitive information.

Synthetic publisher and OCR fixtures establish contract behavior, not live
authentication or OCR accuracy. Linux execution, production key provisioning,
real OCR and publisher integration remain acceptance work. See the
[refill runbook](../local-privacy-refill.md) for evidence and handoff limitations.
