# Local encrypted mapping lifecycle

Phase 5 implements real AEAD encryption, an ephemeral key-provider adapter,
immutable ciphertext storage and create/read/delete application operations.
Linux retains its explicit adapter; macOS now has `MacOSEncryptedMappingStore`.
See the [exact-export follow-up](pr37-exact-export-coordination.md) for current
native-host tests and composition. The dated Phase 5 results below are historical.
Production key provisioning and actual Linux filesystem acceptance are pending.
Do not describe the current Windows environment as secure persistent storage.

## Composition and authority

Install the declared `privacy` optional dependency into a repository-local
environment, or use the development dependencies. No runtime download occurs.
The adapter uses `cryptography` AESGCM and HKDF-SHA256; exact format decisions are
in [ADR 0036](adr/0036-encrypted-local-mapping.md).

Trusted local composition supplies:

- `SessionMappingKeys`, containing keys obtained from a separately approved
  provisioner; `provide(reference, key, lifetime=900)` accepts a random 32-byte
  master key, not a password or caller request;
- `MappingCipher(keys)`, which seals and opens authenticated local records;
- `LinuxEncryptedMappingStore(private_directory, workspace=trusted_repo_root)`,
  using an existing private directory under that root;
- `LocalMappingService` with that store/cipher, the live privacy review service
  as authority, the live sanitized verifier and a trusted UTC clock.

Do not give untrusted code or model tools access to composition, keys, decrypted
records, store methods or the owner's account. Do not place production keys in
environment variables, CLI arguments, logs, files beside ciphertext or checked-in
fixtures. No production input/keystore adapter is implemented here, and no actual
production key has been created or persisted. Session key provision in tests uses
fresh test-only random keys and does not establish production readiness.

On Linux, the caller must create the intended store directory with mode 0700
under an owned repository path. The store rejects unsafe ancestors, symlinks,
wrong owners and group/other write access. Its files must be mode 0600. On Windows
the constructor raises `mapping_platform_unavailable` before creating any files.
There is no fallback that claims equivalent Windows ACL or race guarantees.

## Operations

| Operation | Behavior |
| --- | --- |
| `service.create(command, approval, manifest, key_reference=..., retention=...)` | Requires current human approval and a verified sanitized manifest; encrypts first, rechecks gates and publishes a new immutable map. Returns a local handle. |
| `service.read(handle)` | Requires an unlocked key, unexpired map and exact authenticated handle/envelope/payload binding. Returns local plaintext in memory only. |
| `service.delete(handle)` | Deletes only the file with the exact ciphertext digest and case/map IDs. Works after expiry or key lock; no recursive sweep. |
| `keys.lock(reference)` / `keys.lock()` | Wipes owned buffers and releases one/all keys. Copies and OS memory history are outside that guarantee. |
| `store.close()` | Releases the held directory FD. Key lock and source/review lifetimes remain separate. |

Default retention is one day, with a seven-day maximum. An expired map cannot
be read, but can be deleted with its exact handle. A change to data or retention
requires a newly issued map; there is no update/overwrite method. Concurrent
operations, conflicting publication and mismatched delete digests fail closed.
The default session key unlock window is 15 minutes and cannot exceed one hour.

The encrypted payload contains the exact confirmed command and sanitized
manifest, preserving raw evidence, dismissed candidates/reasons and source/crop
identity locally. Its ordered redaction selections correspond to manifest
occurrences. No approval is inferred from decrypted data; later refill still
requires independent artifact provenance and writer authorization. Source PDFs
and crops are not copied into the map. Restart recovery for source snapshots
and real persistent key recovery remain separate work.

All mapping records, handles and envelopes stay local, including encrypted files.
Never include them, original hashes or key references in cloud metadata or a
sanitized bundle. Display only fixed `MappingFault.code` values in error channels;
do not serialize raw validation exceptions or plaintext inputs. The public
privacy-v1/cloud service-v1 schemas are unchanged.

## Failure and recovery behavior

Wrong key, nonce, ciphertext, authenticated header or cross-case identity refuses
plaintext. The local handle also detects ciphertext replacement before decryption.
Store reads reject partial/noncanonical envelopes, unsafe links, FIFOs, changed
inodes/timestamps and files over the size limit. Encryption occurs entirely before
writing an exclusive temporary ciphertext file; no plaintext staging is used.

Publication is atomic and never overwrites another map. Process crashes may leave
encrypted temporary files or an already published record after a reported failure.
The implementation only cleans up the exact temporary inode owned by an active
write. It does not automatically remove crash leftovers or unrelated task files.
Retain local handles for precise retention deletion; diagnose crash leftovers
locally before an explicitly scoped recovery action. Deletion is unlinking, not
secure disk erasure. External snapshot rollback and compromised owner/root are
outside this trust boundary.

## Contracts and tests

`schemas/local-privacy-mapping-v1.json` defines `LocalMappingRecord` and
`LocalMappingHandle`. The existing local envelope shape is unchanged, but its
description now reflects implementation. The synthetic plaintext example is
explicitly a contract fixture; real records must never be written by that exporter.

```bash
python scripts/export_privacy_mapping_contracts.py
python scripts/export_privacy_contracts.py
python -m pytest tests/unit/test_privacy_mapping.py --basetemp=artifacts/privacy-mapping-tests
```

Create the basetemp parent and keep TEMP/TMP/cache/coverage paths under ignored
repository artifacts. Linux-only tests cover real private permissions, no-follow
paths, atomic publication, conflicts, interrupted writes, expiry and deletion.
They are skipped on Windows; service tests use an explicitly named ciphertext
storage double and real cryptography. Neither those doubles nor cross-target
type checking establish Linux security acceptance.

## Development validation, 2026-09-10

Uncommitted local work on `feat/local-privacy-pipeline`, based on
`8591bddc76584ad630774f214c7452c397c937d5`. Host: Windows / Python 3.12.3.

| Check | Observed result |
| --- | --- |
| Phase 5 focused tests | 32 passed using real AEAD; 8 Linux filesystem tests skipped |
| Combined mapping and Phase 1 contract tests | 101 passed, 8 skipped |
| Full pytest with existing coverage settings | 808 passed, 40 failed, 43 errors, 9 skipped, 2 warnings; 77% aggregate coverage |
| Failure comparison with Phase 4 | Exact same 83 failed/error node IDs; no additions or removals |
| Ruff lint and format | Passed |
| `mypy src --platform linux` | Passed; 96 source files; type analysis only |
| Native Windows mypy | Same three pre-existing POSIX API errors in the business approval adapter |
| Local/mocked cloud tests | 8 passed; no live AWS |
| Submission and branch checks | Passed; zero outgoing commits |
| Actual Linux private storage / production key provisioning | Not performed; Windows storage is explicitly blocked |

`cryptography 50.0.1`, `cffi 2.1.1` and `pycparser 3.0` were installed as binary
packages into the repository `.venv`; download cache and temporary paths were
confined to `artifacts/issue-22-p5/`. No global interpreter, tool, driver, OS
keystore or production secret was changed. The optional/development dependency
declaration is recorded in `pyproject.toml`.

The full run used `--basetemp=artifacts/issue-22-p5/full`,
`-o cache_dir=artifacts/issue-22-p5/full-cache` and
`--cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing`.
Ignored logs, coverage data and exact failure comparison remain under
`artifacts/issue-22-p5/`. The nine skips comprise the eight new Linux mapping
cases and the existing Linux acquisition case. Aggregate coverage includes
unexecuted Linux storage code and isolated PDF worker lines; those were not
replaced by filesystem mocks or excluded to improve the percentage.

No competition originals were modified or uploaded. No commit, push or remote
mutation occurred. CloudFormation lint remains unavailable from Phase 0; this
phase changes no infrastructure. Secure persistent storage is not claimed complete
until approved real key provisioning and Linux filesystem acceptance are available.
