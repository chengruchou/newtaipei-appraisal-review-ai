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

## Commit and remote publication approval

- Implementation instructions such as "start fixing," "continue," or "make the
  changes" authorize only local working-tree edits and relevant local checks.
  Never infer permission to commit, push, publish, or mutate remote state from
  them.
- Do not create a Git commit unless the user explicitly asks for or approves
  committing the current changes.
- Do not push commits, branches, tags, or other refs unless the user explicitly
  approves that specific push and its target.
- Never create, update, or otherwise mutate a pull request without the user's
  explicit approval for the specific remote action. This includes opening or
  closing a pull request; changing its title, body, base, or head; posting,
  editing, replying to, resolving, or deleting comments and review threads;
  submitting reviews; requesting reviewers; changing labels, assignees, or
  milestones; rerunning CI; and merging.
- Treat commit approval, push approval, and pull-request interaction approval as
  separate permission boundaries. Approval for one does not imply approval for
  another, and earlier approval applies only to the stated action and scope.
- Read-only inspection of pull-request status, comments, reviews, diffs, and CI
  results is allowed when it is relevant to the user's request. After local
  changes and checks are complete, report what is ready and wait for explicit
  approval before any commit or remote write.

## No AI attribution in submitted material

- Do not include AI attribution in commits, pull requests, issues, comments,
  code comments, documentation, test reports, or submitted file metadata.
- Do not add AI Co-authored-by, Generated by, Assisted by, or equivalent
  signatures, badges, links, or footers. Do not list Codex, Claude, ChatGPT,
  Copilot, or any other AI tool as author, co-author, committer, or collaborator.
- Use the user's already configured Git identity. Do not fabricate authorship
  or signatures. Disable controllable automatic AI attribution.
- Before every commit, push, or publication, inspect the entire pending content
  and Git metadata. Correct violations before submitting. Include the check
  outcome in the final delivery report.
- Every new work branch must use a functional name, such as feat/, fix/, test/
  or docs/. Do not use AI/tool attribution markers including ai, codex, claude,
  chatgpt, copilot, gpt, gemini, openai or anthropic as naming segments or words.
  Check segment and word boundaries; main and domain are not prohibited merely
  because they contain the letters a and i. Do not rename main or the repository.
- Inspect each outgoing commit's complete content and metadata, the staged
  snapshot, working files, publication text and every intended new/pushed branch.
  Deleting prohibited content in a later commit does not clear an earlier
  outgoing violation. Do not bypass the gate by selecting an incomplete range.
- Policy and negative-test examples must be explicitly identified and narrowly
  scoped as documented in docs/submission-checks.md. They are not signatures.
  Never exempt an entire docs/tests directory or conceal attribution as an example.
- Preserve necessary technical names, model information, and license notices.
  Do not rewrite existing Git history without explicit authorization.
- These requirements supplement existing permissions and human review gates;
  they do not authorize merging, force-pushing, or discarding others' work.
