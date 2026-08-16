# Contributing

## Branches

Create focused branches from `main`:

```text
feat/<short-description>
fix/<short-description>
docs/<short-description>
```

## Local checks

Before opening a pull request, run:

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

## Engineering rules

1. Do not commit real appraisal cases, competition-provided PDFs, credentials,
   account IDs, or temporary AWS URLs.
2. All extracted fields must retain evidence provenance.
3. Numeric and cross-form checks belong in deterministic rules.
4. Bedrock output must be treated as an explanation or proposal, never as the
   source of truth for a pass/fail decision.
5. Every new rule requires pass, fail, and missing-evidence tests.
6. Pull requests that change a data contract or trust boundary must add or
   update an ADR under `docs/adr/`.
