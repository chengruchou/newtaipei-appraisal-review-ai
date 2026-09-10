# Local-only synthetic scan fixture

`blocked.json` validates against `#/$defs/PrivacyScanReport` in
`schemas/local-privacy-scan-v1.json`. It is a synthetic coverage proposal, not a
real source document, executed OCR result, approval or exportable artifact.

All scan records remain local because real reports contain original text,
source hashes and model provenance. Do not combine this schema with the public
privacy manifest or existing service-v1 upload payloads. Regenerate using
`python scripts/export_privacy_scan_contracts.py`.
