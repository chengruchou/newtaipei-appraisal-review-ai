# Exact local privacy export coordination

This follow-up repairs unchanged-region evidence loss and completes the local
mapping-before-transfer boundary. It does not add a cloud endpoint or change the
public privacy-v1, service, document, task, revision or artifact schemas.

## Composition interface and migration

`LocalPrivacyExportGate` remains the single coordinator. Its constructor now
requires `mapping_service: LocalMappingService` and `key_reference: UUID`, in
addition to the existing builder, verifier, live review authority, confirmation
and sink. Existing composition must supply these dependencies; omission fails at
construction rather than silently permitting an unmapped transfer.

```python
gate = LocalPrivacyExportGate(
    builder=builder,
    verifier=verifier,
    authority=review_service,
    confirmation=local_confirmation,
    sink=authorized_document_sink,
    mapping_service=mapping_service,
    key_reference=local_key_reference,
)
manifest = gate.export(command, approval, reviewer_text=local_text)
local_mapping_handle = gate.mapping_handle
```

All arguments above are trusted local composition. The HTTP bridge owner binds
session, operator, Origin and allowed source selection independently. Do not
construct adapters, choose a key, accept a path or trust an approval from arbitrary
HTTP bodies. The confirmation port must show the exact payload and acquire human
consent; a generic request boolean is insufficient. The sink receives only the
existing immutable `PrivacyExportPayload` and must use the canonical authorized
document admission service. The bridge/C2 assembly is a separate integration.

Each call executes this sequence under the gate's session lock:

1. Validate the reviewed command and approval; prepare optional text locally.
2. Build and verify exactly once, freezing PDF bytes and public manifest JSON.
3. Create the encrypted map for that exact command and manifest, including the
   actual occurrence IDs from this build. No second build supplies the mapping.
4. Read and authenticate the stored ciphertext; compare the complete decrypted
   command and manifest to this export, not just case ID or document digest.
5. Present the exact immutable payload to the trusted confirmation adapter.
6. Read/authenticate the same map again, then recheck current approval, verifier
   and payload integrity immediately before calling the sink once.

Key lock/expiry, encryption, storage, readback, mismatch, confirmation and final
admission failures all prevent the sink call. A successful return retains the
existing `PrivacyManifest` return type. `mapping_handle` is local-only and refers
to the latest map created, including after confirmation or sink failure. It is
`None` when no map was created in the current call. The owner must retain each
handle locally before another export if it needs that attempt for reconciliation.

A sink exception can mean an unknown remote outcome. The gate does not retry,
delete the map, claim delivery, advance business status or implement a distributed
transaction. The owner must reconcile canonical admission receipts before a new
transfer. This coordinator does not provide persistent session/key recovery.

## Separate original and sanitized identity

| Local private identity | Public sanitized identity |
| --- | --- |
| `command.source.snapshot_id`, `source_revision`, `source_digest`, source byte size and page transforms | `manifest.sanitized_digest`, sanitized byte size/pages and exact occurrence IDs |
| Original raw values, confidence, crop IDs and detector provenance | Opaque entity tokens and sanitized PDF bytes |
| Encrypted map, map handle, key reference and original/refilled PDFs | Fixed `sanitized.pdf` filename and explicitly reviewed sanitized text |

The opaque case/document IDs express lineage; they do not identify original and
sanitized bytes interchangeably. Canonical cloud admission must bind its own
immutable version/hash to the sink PDF and the public sanitized digest. Never
substitute the original digest or upload the local map/handle/paths as metadata.
Refill reads the encrypted mapping and verifies the separately authorized output
artifact/plan; possessing a mapping does not grant publication or restoration.

## Review evidence behavior

No-op, entity-only and category-only edits preserve the full candidate evidence
when the region is exactly unchanged: raw text, crop ID, confidence, detector
identity/version and page coordinates. Category changes replace only that field.
The edit still advances the revision, clears page review and invalidates approval.
The operator must review pages again and obtain a fresh exact approval.

Moved regions remain new manual crop-only evidence. Text and confidence from the
old geometry are not carried into a newly selected area. Sanitization, residual
canary verification and text refill consume the same preserved command evidence.

## Native local ciphertext adapter

Linux continues to use `LinuxEncryptedMappingStore`. macOS composition can use
`MacOSEncryptedMappingStore` from the same module. Both use the shared POSIX
implementation with explicit platform guards: owned confined directory, 0700 map
directory, no-follow opens, 0600 single-link regular files, exclusive ciphertext
publication, file/directory fsync and authenticated readback. No plaintext staging
or automatic key files are added. Windows remains unsupported by these adapters.

The macOS tests execute actual ciphertext writes and store reopening on macOS;
they do not establish Linux namespace isolation, production key provisioning,
secure deletion, filesystem rollback resistance or restart recovery of the full
review session. Keys in tests are generated locally and retained only in memory.

## Reproduction and evidence

Use a repository-local environment with the declared `dev`, `pdf`, `privacy` and
`aws` extras. Isolated PDF workers use `python -I`; install this exact worktree
editable in that environment so child processes load the intended code.

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest \
  tests/unit/test_privacy_review_preservation.py \
  tests/unit/test_privacy_export_mapping.py \
  tests/unit/test_privacy_mapping_native.py \
  --basetemp=artifacts/pr37-checks/tmp/regressions
```

The valid original-head preservation regression failed 15 cases: six raw-evidence
losses, three residual canaries erroneously accepted, and six actual PDF text
restorations blocked. The original export implementation failed eight mapping
coordination cases. The initial preservation fixture-validation failures are
excluded from defect evidence. The native adapter's three initial failures
established that no macOS adapter existed, not a defect in Linux file handling.

Evidence logs and synthetic PDFs are kept under ignored `artifacts/pr37-checks`.
OCR and human confirmation are explicit doubles; PDF parse/render/refill and
AEAD/native ciphertext storage execute real implementations. Network/AWS/model
acceptance, formal human consent and complete bridge/C2 integration remain
separate from this local test result.

### Local validation checkpoint

On macOS/Python 3.13.5, the working-tree follow-up based on
`ec8367aff74a0a750a89b87a9772e15fbb517e22` passed:

- `ruff check .` and `ruff format --check .` (233 Python files).
- `mypy src` (105 source files).
- `python -m pytest tests cloud_tests --tb=short`: 1018 passed, 10 existing
  platform-specific skips, 7 deprecation warnings, 158.84 seconds.
- The native ciphertext suite: 7 passed, including fresh-process authenticated
  readback and zero transfer after permissions/link/deletion changes.
- Submission inspection against `origin/main`: all five existing outgoing
  commits, full snapshots/index/working files, functional branch and configured
  identity passed. No new commit or remote publication was performed at this
  checkpoint.

The warnings concern Starlette/httpx, AnyIO and PyMuPDF/SWIG. No assertions,
existing skips or confidence thresholds were weakened. These are local results;
GitHub CI, Linux namespace acceptance and real AWS/model calls were not run.
