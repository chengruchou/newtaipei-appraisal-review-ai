# ADR 0032: Local privacy contracts and a separate sanitized boundary

Status: proposed; Phase 1 local implementation, 2026-09-10.
Baseline: `8591bddc76584ad630774f214c7452c397c937d5` on refreshed `origin/main`.

## Context

Issue 22 requires original documents to stay local: local extraction/OCR and
sensitive-region detection, human confirmation, actual removal and tokenization,
independent verification, sanitized export, and eventual local-only refill.
Existing service-v1 DTOs contain evidence text and original document digests.
They are not privacy-safe merely because they omit storage URIs. Existing PDF
correction and overlay operations are not a general sanitizer.

The runtime acceptance target is Linux. Windows is a development accommodation;
its existing POSIX reviewer failures do not block provider-neutral contract work.
Native Windows secret storage, identity and complete runtime support are not
promised. No existing approval gate is weakened to accommodate development.

## Decision

### Data separation and lineage

Use separate strict, frozen Pydantic roots for local records and public manifests.
Export `schemas/privacy-v1.json` independently from `schemas/local-privacy-v1.json`.
Do not add privacy fields to the extra-forbid service-v1 contract. Local source
snapshots contain original byte identity, size, revision and page transforms.
The future reader owns immutable bytes and the local source-file registry;
snapshot IDs are not filesystem paths or caller-authorized document references.

Candidates retain exact local text or an opaque local crop reference, category,
region, detector identity/version and nullable confidence. Unknown confidence is
not zero or success. The future local detector must fail partial/unsupported
scans explicitly; it cannot represent an incomplete scan as an empty success.
Selections preserve dismissed detections with reasons and explicitly assigned
entity groups. Equal names must not automatically imply equal entities.

Only a sanitized manifest, fixed error, and future verified sanitized bytes may
cross the public boundary. Manifest fields are an allowlist: versions, random
case/document IDs, sanitized digest/size, fixed filename/media type, sanitized
pages and opaque entity/occurrence IDs with sanitized coordinates. No original
hash, text, path, crop, reviewer identity, key reference or mapping is public.
`public_manifest_json` rejects local records and subclasses and revalidates the
exact public type. DTO shape alone cannot detect a secret deliberately encoded
in a valid UUID/hash; trusted issuance and byte verification remain mandatory.

All IDs declared UUID4 must be randomly issued in production. Each case gets an
independent namespace and new entity IDs; never hash raw values to produce them.
`PT_<entity UUID hex>` is the rendering convention. The same entity can have
multiple occurrences, each with a distinct occurrence ID. Source and sanitized
digests are separate lineages; local storage alone connects them. Revision and
selection digests remain local. The privacy case/document IDs are privacy-owned;
future service integration needs an explicit sanitized-reference adapter.

### Coordinates and refill

Regions use one-based page numbers and unrotated CropBox bottom-left PDF points.
Local source pages retain original rotation and CropBox origin. Sanitized pages
carry their own dimensions; do not assume sanitized geometry equals the original.
The parser/renderer adapter will own top-left pixels, DPI and rotation transforms,
with finite-number, dimension, clipping and resource-limit tests in Phase 2.

The refill contract is a proposed local plan tied to case, document, map, run,
revision, artifact digest, template digest and writer contract version. It uses
explicit output fields and occurrences with validated destinations. Omission
must be explicit. Repeated entities are legal, repeated occurrences/fields are
not. Contracts with #26/#28 still need agreement; these UUID field IDs are not
a claim that existing PDFField or ArtifactManifest consumers already accept them.
Original image crops may restore a document image only, never create a new
signature or preserve a modified digital signature's validity. Final PDF bytes
remain local and do not confer business approval or a publication grant.

### Authority and lifecycle

`PrivacyReviewCommand` is local consumer input. It has no workflow state, actor,
approval, verification or export flag. A trusted human authority supplies
`LocalPrivacyApproval`; constructing that DTO does not create human authority.
The digest binds the complete source, policy digest, selection revision, evidence,
decisions, entity grouping and reviewed-page set. `privacy-review-json-v1` is
Pydantic JSON mode, sorted keys, compact separators, ASCII escaping, UTF-8 and
SHA-256, with array order significant. Any edit requires new confirmation.
Future policy selection must be trusted and versioned; no policy engine is
implemented in Phase 1.

The lifecycle below is reserved for the future local application service. The
enum does not implement a state store, orchestrator or agent decision loop.

| Transition | Required evidence / authority |
| --- | --- |
| loaded -> scanned | Owned source snapshot; every page processed by supported local capabilities |
| scanned -> awaiting_confirmation | Complete candidate inventory, including manual-review requirements |
| awaiting_confirmation -> confirmed | Actual human reviews all pages and the exact revision; zero detections still require review |
| confirmed -> sanitized | Unchanged confirmation; actual removal and freshly rebuilt output |
| sanitized -> verified | Independent verifier checks the same immutable bytes and complete removal surfaces |
| verified -> exportable | Same revision and unexpired, nonrevoked human approval; trusted export boundary |
| any active state -> blocked | Missing capability, invalid evidence, tamper, stale identity or verification failure |
| edited source/policy/selection -> awaiting_confirmation | New revision; old confirmation and downstream eligibility invalidated |

Phase 1 implements a pure admission check only. It revalidates typed inputs,
all-page review, exact binding, time window and manifest identity. It requires
the same page count and one manifest occurrence per approved redaction selection,
comparing entity multiplicities while allowing repeated entities. It then calls
separately injected approval and artifact-verification ports. Those ports must
check actual issuance/revocation and owned bytes/lineage. They return literal
`True` to allow admission; errors and truthy substitutes fail closed. Public
errors are fixed codes without exception contents. No default approving adapter
exists. A future executor must atomically export the same immutable verified
snapshot and recheck current revisions/revocation; a previous guard call is not
a reusable export credential. No route or uploader is added.

### Encryption and keys

Reserve an AES-256-GCM envelope with a 96-bit nonce, ciphertext including tag,
random map/case/key references, expiry and an AAD format version. In Phase 5,
authenticate the complete header (schema, algorithm, context version, case ID,
map ID, key reference, nonce and expiry) using sorted compact ASCII JSON encoded
as UTF-8. Never accept expiry or case fields before authenticated decryption.
Store original values, source digest and all source-to-token mapping inside the
encrypted payload; do not publish the envelope or key reference.

Use a maintained AEAD adapter with a unique nonce per key and explicit key
provisioning. No cipher or key derivation is implemented here. A key-provider
port is separate from the mapping store. Future Linux storage must enforce
confined paths, ownership/modes, race-resistant access and atomic writes.
Interactive unlock or an OS-backed provider needs its own concrete design;
production keys cannot reside beside ciphertext. Expired maps must reject use.
Deletion does not promise secure erasure on SSD, swap, backups or crash dumps.

## Consequences and validation

The checked-in examples are synthetic contract proposals, including invented
approval and ciphertext values. They are not encryption, authorization or privacy
acceptance evidence. Unit tests use explicitly mocked authority/verifier ports.
They cover strict public shape, geometry, occurrence uniqueness, stale approvals,
all-page review, fixed errors and current schemas. Existing service contracts,
source-binding checks, business approval and writer completion gates stay intact.

OCR, privacy detection, approval issuance/storage, redaction, independent content
verification, encrypted storage, refill and export remain unimplemented. Linux
runtime execution and #25/#26/#27/#28/#31 integration are separate acceptance
work. See [privacy contracts](../privacy-contracts.md) for reproduction and scope.
