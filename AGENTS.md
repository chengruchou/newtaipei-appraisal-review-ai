# Repository Instructions for Coding Agents

## Project goal

Build an agentic, evidence-grounded real estate valuation review system. The
system parses valuation documents, converts case-specific evaluation criteria
into versioned executable rules, verifies factor grades and correction rates,
and produces review findings, an audit trail, and optionally a completed or
corrected PDF.

The competition problem is case review; PDF filling is only one output.

## Sources of truth

When available locally, inspect these documents without modifying or committing
them:

- the official New Taipei City competition brief;
- the sample evaluation-basis PDF;
- the sample valuation forms PDF.

The competition brief defines the problem. Evaluation-basis and form PDFs are
examples, not universal policy. Applicable rules may vary by district,
land-use category, effective date, or case.

## Architectural rules

- AI understands documents; deterministic code owns intervals, grades,
  correction matrices, arithmetic, verification, and PDF writing.
- The agent must make explicit tool and state decisions. Do not describe a fixed
  script or decorative chat interface as an agent.
- Keep domain logic provider-neutral. OCR, LLM, storage, orchestration, and PDF
  libraries belong behind ports and adapters.
- Store case-specific policy in versioned rule data. Do not hard-code the
  Jinshan commercial-land example as universal logic.
- Preserve useful legacy code and migrate it with focused changes.

## Reliability requirements

- Never invent missing facts, units, grades, rules, or evidence.
- Preserve raw text, source file, one-based page number, coordinates, and
  confidence for extracted facts.
- Missing or low-confidence critical input becomes `needs_review`.
- Every evaluation identifies its rule and deterministic calculation trace.
- Unknown factors and unsupported rule formats fail explicitly.
- A case cannot become `completed` after critical validation failure.
- Keep verified facts, inferred classifications, warnings, and unresolved items
  distinct in contracts and reports.
- Preserve the original PDF. Writers create a new file using configured field
  coordinates; never ask an LLM to recreate a visually similar form.

## Repository conventions

- Write repository documentation, filenames, code, comments, schemas, and
  configuration keys in English. Chat responses may use Traditional Chinese.
- Use Python 3.11+, Pydantic 2, FastAPI, Ruff, mypy, and pytest conventions
  already established in `pyproject.toml`.
- Do not commit real cases, competition PDFs, secrets, credentials, private
  URLs, generated PDFs, OCR dumps, caches, or local cloud artifacts.
- Do not modify files outside this repository or alter global tools and drivers.
- Do not overstate implementation status. Separate implemented core behavior,
  scaffolding, MVP scope, and future work.

## Test expectations

Add focused tests for every implemented rule or state transition. Relevant
coverage includes interval boundaries, unit conversion, semantic categories,
distance ranges, matrix orientation, totals, unknown factors, missing evidence,
low confidence, PDF field-map lookup, and completion gating. Run Ruff, mypy,
and pytest when available and report failures honestly.

Changes to a data contract or trust boundary require updated documentation and,
when the decision is durable, an ADR under `docs/adr/`.
