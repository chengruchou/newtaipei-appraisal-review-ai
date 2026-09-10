# ADR 0016: Local privacy review and ephemeral human authority

Status: Accepted for the local SDK. Linux interactive acceptance remains pending.

## Context

Issue 22 requires human review of all sensitive categories on every original
page. A matching digest or caller-supplied reviewer name does not establish
authorization. Issue 25 needs a callable local service without exposing originals
in the AWS API.

## Decision

`LocalPrivacyReviewService` owns one active immutable source, trusted scan,
selections, monotonically increasing revision, page attestations and an in-memory
approval. Scanner, source store, preview provider, human adapter and clock belong
to trusted composition, never consumer payloads. No HTTP or model tools are added.

Every scan attempt immediately revokes approval, including failed scans and
source/policy changes. Overlapping scans cannot install stale results.
Add/edit/remove clear all page attestations and preview markers. Page review
advances the revision and revokes approval while retaining other page marks.
It requires a preview fetched since the most recent selection change. This
tracks delivery and attestation; it cannot measure whether a person saw the image.

Original detector evidence remains in the scan and change history. Edited
selections become manual crop proposals with unknown confidence, linked by
candidate ID, instead of assigning detector text to new coordinates. Removal
is dismissal with a nonblank reason. Crop references resolve only within the
active source/revision. Entity IDs are random and service-issued; grouping is
explicit rather than inferred from text.

Confirmation requires a complete non-blocked scan, all pages attested, exact
case/snapshot/revision/digest and a separate trusted human interaction. The Linux
adapter resolves OS identity, checks the configured owner UID and foreground
controlling terminal, then requests a fresh challenge through `/dev/tty`. It has
no stdin, argument, JSON or environment approval fallback. The trusted consumer
must display the original pages, selections and reasons before prompting. The
terminal prints IDs, version, digest and counts, not original text.

The owning OS account, terminal and composition are trusted. This cannot
distinguish a human from software controlling that same account/terminal; keep
them isolated from untrusted model execution. TTY presence is not claimed as
proof of biological identity. Windows gets no automatic authorization fallback.

After interaction, the service rechecks the active command and confirmation
attempt. Edits, rescans, close and newer prompts invalidate older attempts.
Receipts expire after 15 minutes. Authority checks validate the exact issued
record, current command, owned source and supplied plus trusted current time.
Serialized receipts have no authority in a new service instance. Close/process
exit revoke them. Clock and process memory belong to the trusted boundary;
there is no durable signing, encryption or change to business approval storage.

## Consequences

The pure admission check can use this service as its approval port, but still
requires an independent sanitized artifact verifier. Human approval is not
sanitization, verification, export permission or case completion. Sanitizer,
exporter, encrypted map, frontend and persistent credentials remain future work.

Calls use a process-local lock. Scan and human interactions recheck their version
after completing outside that lock. Each active scan permits up to 1,000 review
events and 10,000 edited selections before rescan is needed. Rescan replaces
the current scan's history; this is not a durable compliance audit store.
Source quotas and PDF subprocess limits apply separately. Close the review and
release owned sources after use; no secure RAM/swap erasure is claimed.

The synthetic harness always denies human approval. Trusted human/terminal test
doubles establish state logic, not actual Linux/operator acceptance. See the
[#25 handoff](../local-privacy-review.md).
