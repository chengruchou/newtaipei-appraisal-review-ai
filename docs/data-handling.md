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
