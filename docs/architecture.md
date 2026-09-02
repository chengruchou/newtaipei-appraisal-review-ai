# Architecture

## Architectural style

The system is a neuro-symbolic agent workflow:

- document AI and structured LLM output propose interpretations of documents;
- deterministic code owns classification, matrices, arithmetic, verification,
  workflow completion, and PDF placement;
- a controller selects tools and safe next states based on evidence and errors;
- human review is an explicit branch, not an exception hidden from the result.

## End-to-end data flow

```text
Evaluation criteria PDF
  -> document parser
  -> candidate rule extraction
  -> schema and semantic validation
  -> reviewer approval when required
  -> immutable, versioned rule set

Valuation case PDFs
  -> document parser
  -> normalized facts with evidence
  -> agent controller
       -> rule-set resolver
       -> deterministic factor engine
       -> verifier
       -> PDF writer, only when safe
       -> audit logger
  -> findings + audit trail + optional output PDF
```

## Components

| Component | Responsibility | Authority |
|---|---|---|
| Document parser | Extract text, tables, marks, pages, and coordinates | Probabilistic input |
| Fact extractor | Map document content to typed facts with evidence | Proposed structured data |
| Rule extractor | Map criteria tables to candidate rule data | Proposed structured data |
| Rule-set resolver | Match approved rules to case applicability | Deterministic |
| Factor engine | Classify ranges/categories and query correction matrices | Deterministic |
| Verifier | Check evidence, units, rules, arithmetic, and copied values | Deterministic where possible |
| Agent controller | Choose tools and workflow states based on outcomes | Workflow authority only |
| PDF writer | Write verified values to a copy of a source PDF | Deterministic |
| Audit logger | Record inputs, evidence, rules, decisions, and writes | Deterministic |
| Explanation generator | Explain existing findings | Untrusted presentation output |

## Agent decisions

The controller is not a chat wrapper and not merely a fixed happy-path script.
It must be able to:

- load an approved rule set or route a candidate set for review;
- stop evaluation when critical facts or evidence are missing;
- retry a replaceable extraction tool without changing source facts silently;
- request review for ambiguous applicability, OCR, units, or categories;
- withhold PDF writing after a critical verification failure;
- export an audit event for every state transition and tool result.

Expected conceptual tools are `parse_document`, `extract_facts`,
`load_or_build_rules`, `evaluate_factors`, `verify_results`, `write_pdf`, and
`export_audit_log`.

## Rule portability

The engine supports a bounded rule language rather than district-specific
branches:

- numeric and distance intervals with explicit inclusive/exclusive bounds;
- semantic category mappings and aliases;
- target-grade-by-comparable-grade correction matrices;
- sums and equality checks;
- explicit units and applicability metadata.

Rule-set applicability includes jurisdiction, land-use category, effective
dates, and source-document identity. If no unique approved rule set applies,
the safe result is `needs_review`. New rule shapes must be added explicitly to
the engine and tested; they are not approximated by an LLM.

## Stable boundaries

Domain models and deterministic engines do not import AWS SDKs, OCR libraries,
PDF libraries, or web frameworks. Ports define provider-neutral contracts.
Adapters translate local or cloud services to those contracts. API handlers
validate transport data and invoke application use cases without embedding
valuation policy.

The existing legacy `CanonicalCase` and `RuleEngine` remain a supported local
baseline for sum and equality checks while the typed factor path is developed.

## Error and human-review path

| Condition | Required behavior |
|---|---|
| Missing or low-confidence critical evidence | `needs_review`; no completion |
| Unknown factor ID | Explicit failed result |
| Unsupported rule format | Reject candidate rule set |
| Ambiguous or multiple applicable rule sets | `needs_review` |
| Unit mismatch without known conversion | `needs_review` |
| Interval gap or overlap | Reject rule set before use |
| Missing matrix cell or invalid orientation | Failed result; no completion |
| Arithmetic or cross-form mismatch | Finding and critical completion gate |
| PDF placement/readability failure | Keep review results; fail PDF output |

Verified facts, inferred grades, warnings, and unresolved items remain separate
in data contracts. LLM prose cannot change these statuses.

## PDF writing

The sample forms do not contain AcroForm fields. The MVP strategy is:

```text
original template + configured field map + verified result
  -> transparent overlay
  -> new output PDF
```

Field maps declare one-based pages and PDF bottom-left coordinates. An adapter
must translate OCR/image top-left coordinates explicitly. Writers validate the
source file, page count, target page, placement, overflow, and Chinese font
rendering. The original is never overwritten. A future AcroForm adapter can
implement the same port.

## AWS boundary

The local core must work without AWS credentials. Object storage, document AI,
model inference, agent runtime, serverless endpoints, and observability remain
replaceable adapters until competition account permissions, Regions, quotas,
and available models are known.

The current CDK stack provisions only private versioned input/result buckets
and a case-state table. It is not a deployed review workflow.

## Trust boundaries

1. OCR and LLM output is probabilistic and must retain evidence and confidence.
2. Candidate rules are not executable production policy until validated and
   approved.
3. Deterministic engines exclusively own grades, rates, and totals.
4. PDF output is allowed only after critical verification succeeds.
5. Real documents stay outside Git and are encrypted in approved storage.
6. Every case records rule and source versions for reproducibility.
