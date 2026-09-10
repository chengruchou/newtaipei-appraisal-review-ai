# Synthetic export regression reports

These JSON files illustrate valid `passed`, `failed` and `blocked` report shapes.
They are synthetic, not results of this checkout or evidence of runtime acceptance.
Fixed canary IDs and counts contain no original values, document paths or secrets.

Regenerate with `python scripts/export_privacy_export_contracts.py`. The schema is
`schemas/privacy-leak-report-v1.json`. A report describes test evidence and never
grants export authority. `python_hooks` scope does not imply kernel isolation or
live AWS verification. See the [runbook](../../docs/local-privacy-export.md).
