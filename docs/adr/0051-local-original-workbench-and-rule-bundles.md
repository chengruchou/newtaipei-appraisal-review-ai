# 0051. Local original workbench and pinned rule bundles

Date: 2026-09-12
Status: Implemented core, with real-case acceptance incomplete

## Context

The first local workbench must use actual source PDFs and the existing job,
material-revision, human-response and receipt contracts. A source registry is
not approval, and a downloaded manual is not proof of its applicability. The
local operating mode must not reuse synthetic model responses or claim that
originals have passed cloud data admission.

## Decision

Keep `run_local_workbench.py` as the explicitly synthetic legacy launcher. Add
`prepare_local_case.py` and `run_original_workbench.py` for controlled local
sources. Preparation reparses the configured originals, checks complete registry
identity and exact value/unit anchors, and assembles proposed material. Each
output directory is new and private. Original PDFs are never rewritten.

The local source adapter implements the runtime document-read port separately
from C2 document transfer. It pins the full source reference and material revision
for each run, checks actual bytes on reads, and checks current case/purpose grants
before and after access. It cannot create an admission receipt or upload data.
The production C2 implementation retains its admission boundary.

The local original preview runs on a separate numeric loopback authority. Every
read needs both an independent pairing token and the current case session,
exact Host and Origin, and exact source version/hash. The browser verifies the
returned bytes. Review session access alone is not original-preview pairing.
No original preview URL or original source path is returned in case metadata.

A rule catalog identifies independently versioned general rules and district
basis documents. Each entry specifies its document/version/hash/pages, role,
allowed use, exact district/zone/use/context scope, dates and metadata review
state. Selection requires a unique entry per required role and scope. Missing,
conflicting, mismatched or unknown applicability remains blocked; there is no
latest-version or district fallback. The selected bundle, catalog digest and
case conditions enter the material digest. Primary district criteria remain
explicit; additional executable sources and procedure-only sources retain their
own identities throughout assembly, source-purpose validation and review.

The full catalog snapshot is pinned privately and re-resolved during verification,
so omitting a competing entry from a selected subset cannot bypass ambiguity.
Public case metadata projects only selected sources and condition evidence; it
does not disclose unrelated catalog entries. Every consuming comparison scope
is checked separately. Procedure-only sources cannot authorize factor rules.
The executable selection identifier excludes the material revision counter,
while the complete bundle digest includes it. A fact-only revision therefore
retains its fixed rule version and still receives a new material binding.

The registry is not a universal rule library. Catalog metadata review, fact
confirmation, case-condition confirmation, rule approval, exact-material
approval, completeness and template publication remain different decisions.
The currently supplied case has unresolved conditions and effective periods;
its catalog is a candidate selection, not an approved policy.

Local native/manual proposals preserve raw confidence, source text and original
OCR observations. Verified native-parser anchors may establish
`localization_only` provenance; this is not measured semantic accuracy and
confers no native numeric authority. Known, unambiguous observations can be
presented for explicit side confirmation. Missing/ambiguous observations remain
blocked. A confirmation creates a new immutable material revision and does not
approve the rule bundle or publication. A correction clears confirmations under
the existing revision contract. An original receipt applies only to unchanged
original material; a new revision cannot reuse a confirmation or approval that
fails its exact binding.

Read-only workbench projections expose the current configured jobs, case
conditions, sources, pinned rules, observations and stored paused assessment.
They validate the exact canonical material and live source authority. Public
verification diagnostics remain structured and path-redacted. The new reads do
not replace legacy HTTP or invocation success/error envelopes.

SQLite read views use read transactions and detached validated models rather
than rewriting the entire persisted state. A small cache covers only validation
of exact immutable material/revision JSON. It does not cache permissions, source
bytes, expiration or revocation. Response commits retain their transactional
version/idempotency checks and current local authority check.

The original composition records Controller output per run and attempt. A retry
keeps its predecessor's output immutable, generates distinct task identifiers,
and supersedes abandoned open tasks under the current execution fence. Paused
reads select the attempt from current open task membership. Existing compositions
retain their original single-attempt behavior unless explicitly configured.
Both response commits and fenced task registration recheck current source
purpose grants at the transaction boundary. Confirmation identity uses the
actual OS reviewer, consistent with the existing local authority contract.

The frontend uses server-provided status and allowed responses. Navigation and
polling perform reads only. Frozen response payloads and idempotency keys survive
route changes within the same authorized client. An unknown write is reconciled
by receipt lookup before an explicit retry. Session changes dispose outstanding
requests and clear private in-memory/tab state. The local session is controlled
operator authentication, not public registration.

## Compatibility and limits

Optional material fields are omitted when absent so existing single-source
material digests and approval bindings do not change. Shared schemas, OpenAPI
and frontend generated types move together. Existing synthetic unit regressions
remain separately labelled and do not count as real-case acceptance.

The original runtime performs deterministic review and explicit human handoff;
it is not a validated model-driven agent. No external model or cloud service is
called. It does not issue material/rule approval or bypass the PDF writer gate.
A supported, approved template/map/font and exact material authority are still
required for output; absent capabilities return an honest unavailable state.
The workbench does not imply formal report readiness when coverage or approval
is missing.
