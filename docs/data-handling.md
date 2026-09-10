# Data handling

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
[ADR 0014](adr/0014-local-privacy-boundary.md). Linux is the privacy runtime
acceptance target; Windows development does not establish secure local storage.
The [acceptance matrix](issue-22-acceptance.md) records remaining OCR, key,
Linux, consumer and cloud requirements. Existing transfer paths remain unsuitable
for sensitive cases until that integration is accepted.

- Block public S3 access.
- Require TLS and encryption at rest.
- Grant least-privilege access by workflow role.
- Keep raw documents separate from derived findings.
- Record rule version and source-object version in every result.
- Set retention and deletion policy with the data owner before production use.

## Logs

Logs may contain case IDs, object keys, rule IDs, status, latency, and request
correlation IDs. They must not contain document text, full extracted tables, or
credentials.
