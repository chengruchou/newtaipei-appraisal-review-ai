# Synthetic extraction contract fixtures

These examples exercise wire structure only. All identifiers, hashes, settings,
timings and usage are illustrative, not live measurements or privacy certificates.
No fixture is an approved document, an independently adjudicated golden, a human
response or an instruction to invoke a provider.

The index identifies each schema bundle and model root. Regenerate from the
repository root with `python scripts/export_extraction_contracts.py`. See
[contract authority](../../docs/extraction-contracts.md) for trusted consumer checks
that must accompany schema validation.

- `request.json`: one synthetic page request with closed context.
- `candidate.json`: empty schema-valid candidate; no completeness claim.
- `failed-page.json`: unavailable privacy integration, zero provider attempts and
  a located failure handoff.
- `handoff.json`: requested review, not a persisted task or actual intervention.
- `evaluation.json`: synthetic mocked development schedule preserving page order
  `[2, 1]`; no cost estimate, measured localization or acceptance certification.
