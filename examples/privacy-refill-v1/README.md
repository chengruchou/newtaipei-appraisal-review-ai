# Synthetic local refill handoff

These files propose the #26 output occurrence/field contract and #28 published
artifact binding. They are parsing fixtures only, not authenticated publications,
approved plans or evidence of completed execution. IDs and digests are fixed
synthetic values; there is no corresponding published PDF.

| File | Local model |
| --- | --- |
| `publisher.json` | `PublishedRefillDescriptor` |
| `plan.json` | `RehydrationPlan` |
| `final-manifest.json` | `FinalLocalManifest` |

Regenerate with `python scripts/export_privacy_refill_contracts.py`. Definitions
are in `schemas/local-privacy-refill-v1.json`. Real plans, final manifests and
refilled PDFs remain local. The fixtures do not grant publication or business
approval authority. See [the refill boundary](../../docs/local-privacy-refill.md).
