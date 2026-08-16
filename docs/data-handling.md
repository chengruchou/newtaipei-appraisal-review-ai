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
- Keep raw documents separate from derived findings.
- Record rule version and source-object version in every result.
- Set retention and deletion policy with the data owner before production use.

## Logs

Logs may contain case IDs, object keys, rule IDs, status, latency, and request
correlation IDs. They must not contain document text, full extracted tables, or
credentials.
