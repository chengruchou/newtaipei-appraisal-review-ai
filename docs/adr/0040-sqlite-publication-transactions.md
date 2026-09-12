# ADR 0040: SQLite artifact publication transactions

Status: Proposed for integration review

## Context

The local integrated service runs durable jobs and human tasks in SQLite. A
publication adapter that checks a copied job state or grants authority based on
worker metadata can expose an old attempt's PDF after lease takeover,
cancellation, source replacement or access revocation. The public legacy PDF
manifest also explicitly describes only a single-context local output.

## Decision

Use the existing AWS-free `AttemptArtifactPublisher` and `CommittedResultResolver`
with SQLite BLOB and manifest adapters. Store publication tables beside the
canonical `review_state`. Check current job, run, revision, attempt, lease owner,
fence, lease deadline, result version, cancellation and human-task state in the
same `BEGIN IMMEDIATE` transaction as the manifest write. Do not weaken or replace
the production DynamoDB conditional transaction implementation.

Run source reauthorization outside the transaction, then compare the exact pinned
source/revision state and durable revocation epoch inside it. Download checks use
the current principal before and after object verification. Revocation is sticky
and fences publication before external source/session grants change.

Approval is a constructor-only capability for exact fixed synthetic assets. It
requires PUBLISH permission and current job ownership; it cannot create a human
approval, confirm a fact or change confidence. Model and request data cannot
supply it. Reject conflicting reapproval at a committed fence without changing
the existing grant. Identical retries retain the same immutable object and
manifest identities.

Capture actual writer evidence through a synchronous durable callback before the
Controller returns. Project only completed, independently verified multi-context
Controller output after read-only preflight/reopen checks and exact asset/source
hash validation. The projection never calls the writer. A separate trusted
synthetic service authorizer precedes the exact grant and commit. Persisted
evidence supports restart without treating an existing file as proof of success.

Expose fenced publication using `FencedArtifactManifest` version 2. Preserve the
legacy single-context/local-only manifest unchanged. Public result projection
contains digests and coverage, never local paths or object keys.

## Consequences

Process crashes before commit leave no manifest. Crashes after commit recover by
identical replay. Unpublished local files or immutable staged objects may remain
but are never downloadable as results. Evidence persistence failure blocks
completion. Expired or revoked grants block download even if bytes still exist.
A future reinstatement or arbitrary-document publication flow needs a separate
reviewed authority design; enabling a synthetic flag is insufficient. The runtime
still owns result persistence and terminal job transition.

The implementation depends on the coordinated private `_connect`/`_decode` seam
of the local review store and its serialized state version. Incompatible schema
changes fail closed and require coordinated adapter changes. Tests prove local
SQLite/C2/Controller/PDF behavior; they do not constitute cloud or model acceptance.
