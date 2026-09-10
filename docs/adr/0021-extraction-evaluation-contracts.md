# ADR 0021: Versioned extraction outcomes and reproducible evaluation inputs

Status: proposed and implemented locally for Issue 21 Phase 1, 2026-09-10.

## Context

The existing page extractor validates proposals and canonical source citations,
but its `PageExtraction` success model requires integer token counts and cannot
represent unknown usage or failed pages. The CLI has no integrated privacy/source
authorization gate. Service v1 consumers reject extra fields. Issues 22, 27, 23
and 24 have separate ownership of privacy, storage, goldens and human authority.

Main ends at ADR 0013. The existing local privacy branch uses 0014 through 0020;
0021 avoids that known integration collision without renumbering another branch.
Recheck numbering before publication if additional decisions merge.

## Decision

1. Add `extraction-v1` requests, candidate/failed outcomes, handoff requests,
   configuration, budgets and per-attempt telemetry. Add an `evaluation-v1`
   manifest using those shared configuration components. Preserve existing
   `PageProposal`, `PageExtraction`, `SourceRegistry`, `assemble`, service v1,
   HTTP/invocation and PDF contracts unchanged. Do not coerce unknown usage into
   legacy integer fields; the later assembly adapter must make that boundary
   explicit while reusing the existing candidate assembly.
2. A public source reference identifies sanitized document/version/hash/purpose,
   page count and the external privacy manifest digest/version. It is neither
   a new PrivacyManifest nor an authorization certificate. A reserved resolver
   takes a trusted principal and request, verifies the actual Issue 22 evidence
   and Issue 27 access/version, and returns immutable sanitized bytes and a
   detached parser snapshot. No production resolver is implemented in Phase 1.
3. Keep raw bytes and parser JSON in an internal, non-wire snapshot. Recheck
   digest, identity, role, page count and canonical citation geometry at use.
   The resolver owns provenance: constructing this class or supplying matching
   hashes does not establish sanitization, identity or access rights.
4. Use a closed language/task context. Do not forward arbitrary case identity
   strings or prior model prose. Located handoffs identify the exact run,
   revision, source and page, with optional region/field IDs. They request review;
   only the Issue 24 workflow can accept a human response or grant authority.
5. Record attempts in order. Unknown SDK completion after timeout cannot be
   followed by a replacement attempt. Missing usage remains null. A failed page
   has no candidate and must retain a located failure handoff. Candidate status
   never means verified facts, approved material or completed review.
6. Freeze dataset, split and golden digests separately from ordered sanitized
   inputs, model settings, prompt/schema/rendering identity, limits and scoring
   policy. Include duplicates and failed cases in later scoring; distinguish
   synthetic expectations, independent adjudication, mocked execution and live
   execution. A manifest records declarations; it does not prove independent
   adjudication, enforce AWS spending or implement scoring.

## Chinese extraction capability

Use the existing Bedrock multimodal page path as the initial implementation
direction. No model is selected or declared accessible by this decision. Phase 2
must check the exact model/API, image capability, region and every permitted
inference-profile destination before any authorized live invocation.

Textract is not the Chinese OCR route. Its current published text-detection
language list excludes Chinese, and it does not support vertically aligned text.
Retain its adapter for explicitly supported inputs or a separately justified
auxiliary route; no automatic translation or silent fallback is approved.
These limits were rechecked on 2026-09-10 against
[AWS Textract quotas](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html).
Phase 4 owns executable capability gating and bounded job/pagination handling.

## Consequences

New wire roots have independent schema exports and synthetic consumer fixtures.
Existing strict consumers require an explicit new adapter, not silent extra
fields. No AWS SDK is imported by these contracts or ports. Legacy unsafe
extraction/explanation factories still exist until Phase 2 gates the production
entrypoints; this decision does not certify them for real document transmission.

The contract tests run offline on Windows; Linux remains the deployment baseline.
The previous Windows POSIX reviewer failures are not permission to weaken review
authority. Actual privacy integration, model execution, scorer implementation,
human tasks, deployment and live acceptance remain later-phase obligations.
