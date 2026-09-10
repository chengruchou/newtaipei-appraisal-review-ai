# ADR 0025: Immutable encrypted local mapping and session keys

Status: Accepted for the local implementation. Production key provisioning and
Linux filesystem acceptance remain pending; secure persistent storage is not
claimed complete on the current Windows development host.

## Context

Issue 22 needs local mapping recovery without writing original values, source
hashes or keys into cloud artifacts. The existing envelope was only a structural
contract. Phase 5 must add authenticated encryption, retention, locking and
confined storage while preserving separate human/export/refill authority.

## Decision

Use the maintained `cryptography` AESGCM implementation with a 256-bit key,
96-bit random nonce and full authentication tag. No custom cipher is introduced.
Wrong ciphertext, key, nonce or authenticated data is rejected by AEAD verification.
See the [official AEAD documentation](https://cryptography.io/en/latest/hazmat/primitives/aead/).

A trusted provisioner supplies a random 32-byte master key through the existing
key-provider port. HKDF-SHA256 derives a separate 32-byte key for each map:
salt is the map UUID's 16 bytes; info is the ASCII bytes `privacy-map-key-v1`.
This separates nonce domains across maps; it is not password stretching.
See the [official HKDF documentation](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/#hkdf).

The service issues a fresh UUID4 map ID for every creation. Each encryption uses
fresh OS-generated nonce bytes; callers cannot select a nonce. A cipher instance
refuses to seal the same map ID twice and is bounded to 10,000 attempted IDs.
The store never overwrites an existing map. Across restarts, fresh IDs/nonces
provide cryptographic collision resistance, not a mathematical global uniqueness
registry. Do not reuse a map ID when retrying creation or changing its contents.

AAD is `EncryptedMappingEnvelope.model_dump(mode="json")` excluding only
`ciphertext_hex`, encoded as sorted-key, compact, ASCII-escaped JSON in UTF-8.
It authenticates schema/context version, algorithm, case/map IDs, key reference,
nonce and expiry. The existing context is `privacy-map-aad-v1`. The decrypted
payload is `LocalMappingRecord`, with `mapping_version=privacy-mapping-v1`,
creation/expiry times, exact review command and sanitized manifest. Payload
identity, ordered occurrence associations and retention are revalidated after
decryption. Plaintext is bounded to 8 MB and never staged on disk.

`LocalMappingService.create` requires live Phase 3 human authority and Phase 4
verified-artifact authority before encryption and again before persistence.
It returns a local handle binding exact ciphertext digest, case/map identity,
key reference and expiry. Reads use a trusted current clock and this binding;
altered handles, header/ciphertext replacement and cross-case reuse fail closed.
Mapping possession does not authorize refill, export or business completion.

`SessionMappingKeys` holds up to eight unlocked buffers. Trusted composition may
provide keys for at most one hour, with a 15-minute default measured monotonically.
Expired or missing keys refuse unlock. Explicit lock overwrites owned bytearrays
and releases references. This cannot erase copies, Python memory history or swap.
There is no environment, argument, file, password or OS-keystore key input adapter.
No production secret was provisioned; this is an ephemeral adapter, not a complete
secure persistent key-management system.

## Filesystem boundary

`LinuxEncryptedMappingStore` requires an existing private directory below an
explicit trusted workspace root. Constructor validation rejects Windows and other
platforms, symlinked roots/parents, paths outside that root, wrong ownership and
writable-by-others directories. Traversal uses directory FDs and no-follow opens;
the final directory must be private to the current UID. File names derive only
from validated UUID4 case/map IDs.

Operations combine a nonblocking process-local lock and Linux `flock`; a concurrent
writer fails with a conflict. Files are regular, UID-owned, mode 0600, with one
link. Reads are size-bounded, reject symlinks/hardlinks/FIFOs and check inode and
timestamps before/after reading. The held root must still match its path.

Creation writes ciphertext only to an exclusive random temporary file, flushes
it, publishes via an atomic no-overwrite hard link, removes the temporary name
and flushes the directory. No replacement/update operation exists. In-process
failure cleanup removes only that temporary inode after checking identity.
A process crash can leave an encrypted temporary file or a complete published
record whose caller saw failure; crash recovery is not an automatic directory
sweep. A remaining extra hard link makes reads fail until explicitly recovered.

Deletion requires the exact expected ciphertext digest and case/map IDs, and
unlinks only that checked file. It remains available after expiry or key lock.
No recursive cleanup is provided. Unlinking is not forensic secure erasure.
The owning account, trusted composition and workspace ancestors remain trusted;
the store does not defend against a compromised owner/root or external filesystem
snapshot rollback. Caller-controlled HTTP/model access is not added.

## Consequences

Default mapping retention is one day, with a seven-day maximum. Expiry prevents
use; deletion is explicit so exact handles can be retained by the local consumer.
Changing a mapping or retention creates a new map and requires current approval;
it does not edit ciphertext in place. Crop references still require the original
owned source for later refill; this phase does not persist original PDF/image
bytes or implement restart recovery for source snapshots.

The dependency is an optional `privacy` extra and part of development dependencies.
Encryption tests run the real library with newly generated test-only keys.
Windows can test cryptography and service logic, but filesystem acceptance tests
remain Linux-only. All production key provisioning and OS-keystore changes need
a separate explicit decision/authorization. No such change is made here.

## macOS adapter follow-up

`MacOSEncryptedMappingStore` explicitly selects the same POSIX file confinement
and atomic ciphertext implementation for macOS. `LinuxEncryptedMappingStore`
keeps its Linux-only guard; unsupported hosts do not silently fall back. Tests
exercise actual macOS write/read, cross-process authenticated readback with a
test-provisioned key, and failure on changed permissions/links during export
confirmation. This extends the local adapter selection only; production key
recovery, source/session restart recovery and OS isolation remain separate.
See the [coordination contract](../pr37-exact-export-coordination.md).
