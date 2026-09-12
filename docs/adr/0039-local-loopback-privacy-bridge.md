# ADR 0039: Separate local privacy HTTP authority and exact C2 export

Status: Implemented for local integration; production identity, OCR and deployment
acceptance remain separate.

Numbering note: the integration registry allocates 0039; the originating approval
and acceptance scope above is unchanged.

The local bridge runs as a separate loopback application. Numeric Host, loopback
peer, exact Origin and server-issued session tokens constrain every request.
Source UUIDs refer only to server-selected immutable local files; raw path/URL or
upload DTOs do not cross HTTP. Existing local privacy commands retain their
revision and explicit human-confirmation semantics.

Provisional output is one owned build, retained through exact preview retrieval,
digest confirmation, encrypted mapping persistence/readback and final transfer.
The existing export gate remains authoritative; a supplied digest or schema-valid
manifest never authorizes an independent upload. Original identity stays local;
cloud admission binds a distinct immutable C2 document/version to sanitized bytes.

The C2 sink binds the existing exact presenter to `ConfirmedDocumentExport`, signs
with the key pinned by the real C2 verifier, validates actual PDF bytes, ingests
and reads back before catalog registration. It rejects unsupported optional text
rather than losing a confirmed field. Retained in-process receipts/maps support
unknown-outcome reconciliation, but are not a transaction across these stores.

A successful transfer also records a session-local binding from the complete
canonical public manifest to the exact encrypted mapping handle. Failed
transfer handles remain retained separately for reconciliation and cannot enter
automatic restoration selection. Selecting the bound handle precedes decryption;
an unreadable unrelated map cannot block the authorized artifact. Missing,
corrupted, expired, revoked or mismatched selected evidence still denies access.
No HTTP input can supply or replace this index, and no persistent session-recovery
authority is implied by this in-memory binding.

Local refill accepts only a server-resolved authorized published result and plan.
It uses existing mapping/refill APIs and writes a new confined private file,
protecting both original and downloaded artifacts. No restored artifact returns
to cloud export. Public/shared schemas are unchanged.

See [the bridge contract and evidence](../local-privacy-bridge.md) for exact routes,
configuration, caller migration, tested boundaries and remaining acceptance.
