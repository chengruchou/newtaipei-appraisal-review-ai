# ADR 0042: Competition data admission is separate from privacy confirmation

Status: proposed implementation; no real data or organizer approval granted.

## Context

The environment rules dated 20260722, page 1, general rule 2 prohibit thirteen
categories in the competition AWS account. The original PDF SHA256 is
`64bbda4d8056d3edd913ced8e96330f282621a00fe9d4152341d162fd385aec0`.
Local redaction, a negative detector result and an operator's export confirmation
do not establish permission to introduce the remaining material into that account.

## Decision

`CompetitionDataPolicy` fixes the source version and all thirteen categories.
An exact outbound envelope identifies every immutable part by surface, size and
digest. Each part requires explicit classification of every category, inspection
evidence, source provenance, a named reviewer and a bounded validity period.
Unknown classification, missing evidence, a changed part or an extra part denies
admission. The complete record also needs a separately trusted digest and scoped
reviewer authority; caller-provided records do not approve themselves. Revocation
and expiry are checked again at actual transfer. No HTTP approval endpoint or
automatic creation of an approved classification is provided.

The current conservative profile admits only reviewed material created from
scratch. Real cases and renamed, scaled or redacted derivatives remain local.
Synthetic financial information also remains blocked until an actual organizer
clarification is included in the separately approved exact policy. A financial
clarification cannot permit any other prohibited category. Neither a profile
flag nor a generic human confirmation can override these decisions.

`LocalPrivacyExportGate` checks the configured competition authority before map
creation and after confirmation. `CloudExportSink` independently requires it
before signing and before ingestion, so missing bridge configuration fails
closed. Explicit local rehearsal accepts only the concrete SQLite document
storage; this path proves local behavior and never asserts cloud admission.
Legacy privacy-only local ports remain usable without changing their DTOs.

The physical Bedrock transport checks the exact immutable serialized body and
destination through the same port before every actual send, including implicit
SDK retries. Embedded page images and all prompt text are part of that body.
A previously approved PDF does not authorize newly assembled prompts, extracted
JSON, reviewer text, log payloads, mappings or restored output. Each separately
transmitted envelope needs a complete new review; unsupported outbound tools
must remain disabled in the competition profile.

## Consequences and limits

The financial candidate detector now includes monetary amounts and valuation
labels, but detection only assists local review. It is not a complete semantic
classifier. PDF inspection must examine decoded streams, all page images,
embedded objects and metadata. A text-only scan is insufficient. The carrier's
page-image binding covers its complete PDF, not a claim of OCR accuracy.

`PinnedDataReviewAuthority` consumes preapproved local records with independent
digest/reviewer scope and current revocation; it cannot issue approval. A real
operator workflow, organizer interpretation, source evidence and competition
account acceptance remain necessary. Local tests use explicitly synthetic
authority fixtures and do not approve real material.

The from-scratch arithmetic example is generated without reading case inputs by
`scripts/prepare_competition_demo.py`. It retains valid decimal arithmetic and
labels financial cloud permission as unknown. The original full OCR restoration
case remains a separate regression and cannot be replaced by this example.
