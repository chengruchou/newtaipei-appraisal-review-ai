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

For a blank-value conflict, inspect every arithmetic finding and the independent
expected value. The same candidate must satisfy all of them, including terminal
blanks. Different non-independent proposals remain unresolved, regardless of order.
For source_purpose findings, verify selected forms/criteria identities and citation
use; a rule example is not a case fact. See ADR 0009. Do not repair either condition
by overwriting observations, promoting scores or silently dropping required checks.
