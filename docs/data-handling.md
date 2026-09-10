# Data handling

Issue 21 Phase 2 gates cloud document extraction through the
[authorized snapshot assembly](extraction-preflight.md). Raw-manifest extraction,
legacy explanation and unversioned Textract entrypoints fail closed. Only native
local preparation and the no-network request plan are available without the
actual privacy/source integration; a plan is not an export authorization.

Appraisal documents may contain personal, location, ownership, or financial
information. Treat every real case as sensitive unless the data owner states
otherwise.

## Repository rules

- Do not commit competition-provided PDFs or real cases.
- Commit only synthetic or explicitly redistributable fixtures.
- Do not paste document contents into issues, pull requests, CI logs, or test
  snapshots.
- Store dataset provenance and access instructions, not the files themselves.

## AWS baseline

Issue 22's local privacy pipeline requires original documents, source hashes,
OCR text, page images, sensitive candidates, mappings, keys and refilled final
PDFs to remain in the trusted local component. Only independently verified
sanitized bytes, an allowlisted privacy manifest and explicitly reviewed sanitized
reviewer text may enter cloud processing through the privacy boundary.
Do not upload originals for later redaction or use cloud OCR as a fallback.

The current privacy implementation provides contracts, pure admission checks,
local PDF scanning, a configured OCR adapter and an ephemeral human review
service and a local raster sanitizer with independent verification. The
[export gate](local-privacy-export.md) hands off exactly confirmed immutable bytes
through a trusted sink port; tests use a local sink only. Production confirmation,
Linux network isolation and cloud integration remain pending. Views, dismissal
reasons, crop references
and approval records remain local; see the [#25 SDK handoff](local-privacy-review.md).
See [local sanitization](local-privacy-sanitization.md) for the verified-bundle
boundary and pending actual OCR acceptance. Sanitizer drafts are not upload grants.
The [local encrypted mapping service](local-privacy-mapping.md) retains mapping
plaintext in memory and writes authenticated ciphertext only. Mapping handles,
envelopes and key references also remain local. Production key provisioning and
Linux filesystem acceptance are pending; Windows storage is explicitly blocked.
The [local refill writer](local-privacy-refill.md) creates a new final PDF after
artifact and output-plan validation. Final PDFs and manifests remain local;
refill grants neither upload permission nor business completion authority.
Existing upload paths are not approved for
sensitive cases by this work. Existing evidence-bearing service-v1 DTOs, reviewer
free text, filenames, metadata and errors still need integration with this boundary.
See [privacy contracts](privacy-contracts.md) and
[ADR 0021](adr/0021-local-privacy-boundary.md). Linux is the privacy runtime
acceptance target; Windows development does not establish secure local storage.
The [acceptance matrix](issue-22-acceptance.md) records remaining OCR, key,
Linux, consumer and cloud requirements. Existing transfer paths remain unsuitable
for sensitive cases until that integration is accepted.

- Block public S3 access.
- Require TLS and encryption at rest.
- Grant least-privilege access by workflow role.
- Original documents and re-identification maps stay local. C2 accepts only exact
  sanitized bytes from the trusted local export gate, with a value-free public
  PrivacyManifest and an authenticated, time-bound human export attestation.
- Do not upload original filenames, paths, maps, raw text drafts or receipt/key files.
- Record rule version and source-object version in every result.
- Set retention and deletion policy with the data owner before production use.

## Logs

Document-service audit contains random actor/case/document/run IDs, operation,
timestamp, outcome and a finite error code. Public results and errors contain no
bucket/key, storage URI, private path or raw SDK diagnostic. Do not enable request
body or SDK wire logging in the production gateway. Logs must not contain document
text, original filenames, full extracted tables, mapping values or credentials.
See [document transfer](document-transfer.md) for the explicit trust boundary and
the limits of synthetic canary evidence.
