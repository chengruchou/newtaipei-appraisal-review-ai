# Member A: local entry path

This entry is runnable and synthetic. It does not parse source PDFs, call a
foundation model, deploy AgentCore, or create a PDF. Real adapters remain
#5/#7, full review semantics #8, cloud orchestration #9.

## Fresh checkout

Active entry branch: feat/member-a-entrypoint, PR #13 (replaces #11). It targets
main because foundation #10 has already merged. Runtime preparation is separate
on test/runtime-smoke, PR #14 (replaces #12), based on A. See [migration](pr-migration.md).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,aws]'
export PYTHONPATH="$PWD/src"
python -m appraisal_review.demo verified
python -m appraisal_review.demo completed
python -m appraisal_review.demo needs_review
```

PYTHONPATH explicitly selects this checkout. On this macOS/Python 3.13 host,
editable-install .pth files were marked hidden and skipped by Python; this line
also works around that local environment problem. No global tools were changed.
The demo selects local mode explicitly; imports do not discover AWS credentials.

- verified: calculated synthetic rate 5.0, verification passes, no output requested.
- completed (scenario name): verified/simulated, fake writer called once, pdf_result includes
  `Synthetic PDF writer: no file was created.` and page/field metadata.
- needs_review: missing target evidence, no total or PDF, findings preserved.

Request samples: examples/review-verified.json, examples/review-completed.json,
examples/review-needs-review.json. These are synthetic fixture URIs, not files.
The examples correspond to the Python synthetic_request builder and tests.

## HTTP

```bash
RUNTIME_MODE=local SYNTHETIC_DEMO=true uvicorn appraisal_review.api.app:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail -H 'Content-Type: application/json' --data-binary @examples/review-completed.json http://127.0.0.1:8000/v1/reviews
curl --fail -H 'Content-Type: application/json' --data-binary @examples/review-needs-review.json http://127.0.0.1:8000/v1/reviews
```

Default SYNTHETIC_DEMO=false avoids presenting fixture output as document review.
Without injected adapters a valid review request returns a configuration error;
legacy health/validation still work. This is a loopback/local entry, not the
public cloud API. Do not expose internal document URI input to remote users.

For a self-contained real HTTP smoke (starts and stops its own local server):

```bash
python scripts/http_smoke.py
```

## Composition and Member B handoff

```python
from appraisal_review.application.bootstrap import ReviewAdapters, build_controller
from appraisal_review.config import Settings

# Supply actual implementations, including B's writer when available.
# adapters = ReviewAdapters(mode="local", parser=..., fact_extractor=...,
#                          rule_provider=..., pdf_writer=...)
# controller = build_controller(Settings(), adapters=adapters)
```

The API supports create_app(settings=..., adapters=...) and a
get_controller_factory dependency override. The invocation adapter accepts the
same factory: async invoke(payload, controller_factory=...). Domain request/run
models are shared, not reconstructed into a different transport schema.

B imports PDFWriteRequest/PDFWriteResult and errors from domain.pdf_models,
and PDFWriter from ports.pdf. FakePDFWriter in adapters.local.fake_pdf is the
contract test double. Inject B's implementation into ReviewAdapters.pdf_writer;
application/controller.py invokes it after verification.can_complete. B must
perform real output validation before returning; A validates metadata only.
The PDF operations and value_ref rules are in [PDF contract](pdf-contract.md).

AWS adapters should construct clients explicitly in their AWS composition layer
and inject a mode="aws" bundle. RUNTIME_MODE=aws requires Region, model ID, input
and result buckets, cases table and actual parser/extractor/provider adapters.
Missing configuration fails explicitly, even if credentials happen to exist.
The current Bedrock explanation adapter is not a rule or fact extractor.

## Verification and publishing

```bash
ruff check .
ruff format --check .
mypy src
pytest
python scripts/http_smoke.py
python scripts/check_submission.py
```

The gate checks full outgoing commit trees/metadata, index blobs, working files,
configured Git identity and functional branch names. Pass --publication-file
for a prepared PR/Issue body and --head/--branch when publishing another ref.
See [submission checks](submission-checks.md) for narrow negative examples and
range selection. Human review also checks equivalent signatures, badges and
links. Check before every commit, push or publication; do not rewrite history.

## Schema-2 review migration (#8)

Use the same commands and entry routes. ReviewAdapters now accepts authorization
implementing ports.approval.ReviewAuthorization. Parser results include typed
SourceDocument; the rule provider returns ReviewPolicy and extractor CaseFacts.
Both must be configured for the request case. Factor-only adapters cannot prove
full completion and return needs_review.

The completed synthetic scenario name remains a CLI compatibility alias for
exercising the fake writer. Its actual status is verified, artifact_status is
simulated and output_pdf_uri is null. A successful real writer still yields
completed/written. No writer yields verified/unavailable. Multiple comparisons
are all included in case_review, with unsupported_contexts if PDF was requested.

Run the current complete suite, including subprocess coverage configuration:

```bash
PYTHONPATH=src python -m pytest tests cloud_tests --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing
ruff check .
ruff format --check .
PYTHONPATH=src mypy src
PYTHONPATH=src python scripts/http_smoke.py
```

See ADR 0005 and test_case_review.py for adversarial claims, observed values,
arithmetic, applicability, complete inventory and zero-writer-call examples.

Runtime confidence applies to schema-2 review and independent recalculation:

```bash
MIN_EXTRACTION_CONFIDENCE=0.95 PYTHONPATH=src python scripts/http_smoke.py
```

The setting must be finite and in [0, 1]; default is 0.85. A 0.90 observation
requires review at 0.95 even when its material digest was approved. The existing
synthetic smoke has 0.99 observations; the HTTP composition regression explicitly
checks both thresholds with 0.90 observations. Never increase confidence to pass.

Before approving material, register every slot's complete context and validate its
factor binding. Cross-table checks may join valid contexts. A copied aggregate of
5.005 against a rounded expected 5.00 passes tolerance 0.01, but must still satisfy
any additional stricter check on that target. Grade/factor-rate equality stays exact.

## Real document preparation and review (#7)

Install into the project environment, then use explicit source paths:

```bash
python -m pip install -e '.[dev,documents,aws]'
export PYTHONPATH="$PWD/src"
python -m appraisal_review.document_cli --help
```

Create a private ignored input manifest, with identity (case_id, version, district,
zone, land_use_category, effective_date) and a documents list. Each document needs
absolute path, document_id, version, role, expected_hash and optional document_date.
Roles are criteria/forms/reference/brief. Hashes are lowercase SHA-256, not guessed
version strings. The runner never silently chooses files, AWS profiles or buckets.
Use fresh output directories; existing artifacts are not overwritten.

```bash
python -m appraisal_review.document_cli parse --manifest artifacts/input-manifest.json --output artifacts/run-01
python -m appraisal_review.document_cli native-candidates --manifest artifacts/input-manifest.json --output artifacts/run-02
python -m appraisal_review.document_cli check-golden --manifest artifacts/input-manifest.json --golden artifacts/golden-fields.json --output artifacts/run-03
```

The native candidate report includes source locations, intervals, matrices and
unresolved interpretations. It never grants approval. Golden files must contain
independent manually checked expectations, not the model's own output.

For live extraction, supply project identifiers after validating account access.
The following shell variables must be explicitly set by the operator. No bucket
or jobs table is needed. --page-limit is a hard per-run budget; start with the
representative pages, then explicitly cover the rest of the criteria/forms set.

```bash
python -m appraisal_review.document_cli extract --manifest artifacts/input-manifest.json --output artifacts/live-01 --profile "$PROJECT_PROFILE" --region "$PROJECT_REGION" --expected-account "$PROJECT_ACCOUNT" --expected-role "$PROJECT_ROLE" --model-id "$PROJECT_MODEL" --pages criteria:2,7 forms:1,3 --page-limit 4 --attempts 2 --max-output-tokens 12000
python -m appraisal_review.document_cli assemble --manifest artifacts/input-manifest.json --extractions artifacts/live-complete --output artifacts/candidate-01
python -m appraisal_review.document_cli inspect --material artifacts/candidate-01/material.json --output artifacts/candidate-01/review-copy.md
```

Add --allow-cross-region only when the chosen inference profile and its data
routing have been explicitly selected for this project. The provisional model
capability reference is Claude Sonnet 4.5; no account/model selection is finalized
without the designated account. AWS documents a 200K context and 64K maximum output
for that model; this runner uses smaller bounded page inputs and output limits.
Converse image inputs are bounded to 3.75 MB and 8000 pixels. PDFs are parsed and
rendered locally, so native PDF model payload support is not assumed. Sources:
[AWS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-5.html),
[Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html),
[image/document limits](https://docs.aws.amazon.com/cli/latest/reference/bedrock-runtime/converse.html).
Textract and BDA document language lists do not include Chinese; audio language
lists do not establish document support. Their official limits are linked in
architecture.md. Account availability and actual Chinese table quality remain
live-test requirements.

A human reviewer inspects source pages, matrix cells, applicability, complete
inventory, unknown shapes, original values and all normalized facts. Edit candidate
material to resolve ambiguity; do not delete unresolved required checks. Then:

```bash
python -m appraisal_review.document_cli init-store --store artifacts/reviewer-store
python -m appraisal_review.document_cli confirm-facts --material artifacts/candidate-01/material.json --expected-digest "$INSPECTED_DIGEST" --output artifacts/confirmed-material.json
python -m appraisal_review.document_cli inspect --material artifacts/confirmed-material.json --output artifacts/confirmed-review.md
python -m appraisal_review.document_cli approve --material artifacts/confirmed-material.json --store artifacts/reviewer-store --expected-digest "$CONFIRMED_DIGEST"
python -m appraisal_review.document_cli review --material artifacts/confirmed-material.json --store artifacts/reviewer-store --output artifacts/review-result.json
```

The digest is printed in the review Markdown. confirm-facts is an explicit human
assertion about the inspected facts, not an extraction step; it rejects ambiguous
or missing facts and does not clear unresolved items. approve requires the exact
new digest. The OS identity owning the private store is the authorized reviewer;
there is no user-name flag or model-supplied approval shortcut. No real approval
has been issued by this delivery. Review with a nonexistent store reports pending
approval; it does not create a store or receipt. Review results include observed,
expected, findings and coverage. This CLI supplies no PDF writer and creates no PDF.

After the review correction, extraction derives legacy evidence from each side's
validated source citations. Operators and models do not need to guess source_file
or manually patch evidence before confirmation. conflicting_legacy_evidence means
an explicit legacy location disagreed with its canonical references;
invalid_source_reference means the canonical source is absent or does not resolve.
Neither error is repaired using fabricated locations or another comparison side.
Re-extract and inspect changed candidates, then confirm and approve the new digest
when appropriate. Never reuse an old receipt after normalization or content edits.

The parser uses PyMuPDF under its upstream AGPL/commercial licensing terms; retain
those notices and include the chosen dependency licensing in redistribution review.
[Upstream licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).

For review 5122010147, inspect source_binding failures before retrying an artifact.
Reconcile the actual request/parser/registered identity and roles; do not edit a
policy to authorize a substituted source. Inspect observed_source_binding and
arithmetic_dependency findings at their slot/check IDs. Reconcile source cells
and remove self/cyclic derivations through human-reviewed material changes.
Every change requires fresh exact-material approval. Check rules_loaded followed
by factors_evaluated and results_verified; loaded candidates are not all computed.
See ADR 0007. No AWS account or real approval is needed for the regression suite.

For confidence failures, inspect confidence_kind, provenance and producer before
scores. Unknown is not a low calibrated measurement. A native measured side uses
the minimum of outer and every used evidence score at the runtime threshold.
For human review, use the controlled confirmation operation, inspect its side
binding, then approve the exact final material separately. Preserve low original
scores. Old method-only material needs explicit reconciliation and confirmation;
old receipts do not authorize the new serialization. See ADR 0008.
