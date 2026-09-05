# ADR 0008: Explicit measurement and confirmation provenance

Status: proposed for human review. Contract decision precedes confidence tests.

The previous schema conflated measured fact accuracy with parser localization.
A high outer observation score could conceal low measured evidence; applying a
blanket evidence threshold also rejects legitimate human-confirmed localization.

Reliability now declares confidence_kind (unknown, localization_only, measured)
and provenance (unknown, parser_registry, native_extraction). Defaults are unknown,
never inferred from a zero or high score. Legacy EvidenceRef and legacy HTTP
contracts are unchanged. For a native_numeric side, measured/native_extraction
and a named producer are required. All used evidence entries must bind its complete
canonical source set. The effective measured confidence is the minimum of the
outer observation and every critical evidence score. Calculation and independent
recalculation use the same configured runtime threshold; equality passes. Producer
provenance is a reviewed adapter assertion, not calibrated accuracy certification.

Model self-reported confidence is diagnostic only. The extraction adapter resets
model declarations to model_proposed, localization_only/parser_registry and no
confirmation. It derives location evidence from the parser registry and retains
zero raw confidence; it never copies a model score into measured evidence.

Controlled confirmation preserves all original scores, raw text and references.
It creates a typed local-review-v1 confirmation containing the actual OS reviewer
and a digest of that side's observation, source anchors, context/factor identity
and reliability/provenance. It changes method to reviewer_confirmed. The later
injected exact-material authority must authorize the complete confirmed material.
The calculation and independent verifier receive the same explicit confirmed-side
set; this bypasses only the measured confidence gate, not value/unit/evidence or
interpretation checks. It does not rewrite any measurement or raise its score.
A method string alone, a stale confirmation, or whole-material approval without
resolved locations/values/ambiguities is insufficient. The confirmation record is
not a standalone signature: only trusted exact-material authorization can accept
it. The local OS account/store boundary remains authoritative and must not be
exposed to model tools. Compromise of that trusted reviewer is outside this model.

New fields participate in canonical material serialization and approval digests.
Old material can load for inspection with unknown provenance, but cannot silently
become trusted. Old confirmation-only method strings require explicit fresh
confirmation. Existing receipts no longer match normalized material; there is no
automatic migration, approval or re-signing. Re-extract with current adapters or
have the reviewer explicitly reconcile provenance and source locations, confirm,
inspect the new digest, then approve separately. Result schema 2.0 and shared
PDFWriter request/result stay unchanged; this is a fail-closed material extension.

Synthetic native fixtures explicitly declare measured provenance. Human-flow tests
retain low scores before and after confirmation, exercise missing approval and
post-approval confidence/provenance/citation tampering, and use only isolated test
authorities. Real-case approval and live-model acceptance remain pending.
