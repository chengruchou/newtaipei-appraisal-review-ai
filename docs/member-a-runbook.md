# Member A: local entry path

This entry is runnable and synthetic. It does not parse source PDFs, call a
foundation model, deploy AgentCore, or create a PDF. Real adapters remain
#5/#7, full review semantics #8, cloud orchestration #9.

## Fresh checkout

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
- completed: same calculation, fake writer called once, pdf_result includes
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

The attribution check inspects pending text, outgoing commit metadata and the
configured Git identity. Pass --publication-file for a prepared PR/Issue body.
Human review also checks equivalent signatures, badges and links. Check again
immediately before each commit, push or publication; do not rewrite history.
