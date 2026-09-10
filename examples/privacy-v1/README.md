# Synthetic privacy contract fixtures

These are Phase 1 consumer proposals. `index.json` maps each example to its
Pydantic definition in either `schemas/local-privacy-v1.json` or
`schemas/privacy-v1.json`.

Everything under `local/` is local-only test data. The approval record is
invented, the mapping ciphertext/nonce are dummy bytes, hashes are illustrative,
and UUIDs are fixed synthetic identifiers. None of these records is authority,
proof of encryption, a real document reference or a downloadable artifact.

Only `public/` represents the proposed public allowlist. A valid public manifest
still requires trusted byte verification and the future export boundary before
upload. No production adapter or external consumer integration is implied.

Regenerate from the repository root with
`python scripts/export_privacy_contracts.py`. See
[privacy contracts](../../docs/privacy-contracts.md) for current scope and tests.
