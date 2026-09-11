# Synthetic local mapping contract fixture

`synthetic-record.json` is a deliberately synthetic plaintext example of
`LocalMappingRecord`, validated by `schemas/local-privacy-mapping-v1.json`.
Its identifiers, evidence and dates are test data. It is not a real mapping,
approved review, usable key or ciphertext. Real plaintext records stay in memory
and must never be written by this fixture exporter or uploaded.

Regenerate with `python scripts/export_privacy_mapping_contracts.py`.
