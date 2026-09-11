# Gateway recovery and dependency validation

## Write outcome boundary

Decision: [ADR 0045](../../docs/adr/0045-gateway-write-outcome.md).

A proxy can lose an upstream response after the response transaction committed.
For reviewer writes, a non-success HTTP response is definitive only when its body
validates against the committed `ServiceProblem` schema and its code matches the
canonical status: unauthorized/403, not_found/404, version_conflict/409,
invalid_request/422, execution_failed/500, capability_unavailable/503.

Noncanonical 502/504, HTML error pages, mismatched status/code pairs and malformed
problems are unknown outcomes. So are body interruption, timeout and invalid
success receipts. The existing form retains the detached, deeply frozen command,
including nested correction values/evidence and its original idempotency key.
Inputs remain locked and Send again resends the same command. Only a validated
receipt completes the submission. Validated canonical rejections keep their
existing refusal or conflict/reload behavior. Read-only errors also require a
canonical envelope, and existing TransportError wording is preserved. No DTO,
generated schema, confidence or authority change is
introduced by this repair.

The unit/component regression matrix reproduced 11 failures on
`3af0468db69e8e3f2a861d9cf1c17b8d74212e57` before the fix; the six canonical-error
cases already passed. The real-browser test below uses the actual component
FastAPI app, HumanTaskService, LocalHumanTaskStore and InMemoryJobStore. The
loopback proxy consumes a successful upstream response before returning 502,
504, or a truncated body. It never mocks API routes or manufactures receipts.
Each failed browser response must leave an answered task, only revisions r1/r2,
the same original receipt and an unchanged correction. The final retry must
recover that receipt with byte-identical payload/key and no additional revision.

## Reproduce

Use the declared Python 3.11+ development extras and the npm lock. In an isolated
checkout, with the existing virtual environment selected:

```bash
PYTHONPATH=src:. python web/scripts/gateway_api_fixture.py \
  --manifest artifacts/gateway-browser-fixture.json --port 8769
```

In a separate terminal:

```bash
cd web
npm ci
npm run verify
PLAYWRIGHT_BROWSERS_PATH=../artifacts/playwright-browsers npx playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=../artifacts/playwright-browsers \
  REVIEW_BROWSER_FIXTURE=../artifacts/gateway-browser-fixture.json \
  npm run e2e:real -- gateway-recovery.spec.ts
npm audit --json
npm audit --omit=dev --json
npm run generate
git diff --exit-code -- src/api/schema.d.ts
```

Start a fresh fixture for each browser run; the successful test intentionally
changes the fixture. The fixture principal resolver and proxy controls are only
synthetic test configuration. They establish local component transaction and
browser recovery, not deployed identity validation, durable process restart,
full privacy restoration, hosted CI, or AWS acceptance. Do not deploy the
fixture/proxy or use them with private case material. The integration branch must
retain its canonical service assembly and rerun the integration browser suite.

## Security repair

The pre-repair lock audit reported seven affected packages: five moderate, one
high and one critical. The affected browser runtime dependency was React Router;
the other findings were in development/build/test tooling. The final lock uses:

| Package                         | Resolved version | Scope             | License |
| ------------------------------- | ---------------- | ----------------- | ------- |
| react-router-dom / react-router | 7.18.3           | Browser runtime   | MIT     |
| vite                            | 6.4.3            | Development/build | MIT     |
| esbuild                         | 0.25.12          | Build dependency  | MIT     |
| vitest / @vitest/mocker         | 4.1.11           | Tests             | MIT     |
| @vitejs/plugin-react            | 4.7.0            | Development/build | MIT     |

React remains 18.3.1. These releases have compatible declared peer requirements;
CI's Node 22 remains supported. Vitest configuration now imports `defineConfig`
from `vitest/config` so its test options retain type checking. No overrides,
audit ignores, severity changes, test exclusions or assertion reductions are used.
The complete lock and production-only lock were rescanned separately. Package
metadata and existing license notices are preserved.

Official advisories used to select patched versions:

- [React Router redirect handling](https://github.com/remix-run/react-router/security/advisories/GHSA-wrjc-x8rr-h8h6)
- [React Router SSR error deserialization](https://github.com/remix-run/react-router/security/advisories/GHSA-337j-9hxr-rhxg)
- [Vite Windows path denial bypass](https://github.com/vitejs/vite/security/advisories/GHSA-fx2h-pf6j-xcff)
- [Vitest mock redirect traversal](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9)
- [Vitest UI file access](https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp)
- [esbuild development server origin handling](https://github.com/evanw/esbuild/security/advisories/GHSA-67mh-4wv8-2f99)

The Router SSR issue is not an assertion that this static application runs SSR.
Development-server findings do not establish a production static-bundle exploit.
Repairing and scanning this npm lock does not scan a container image or establish
that other dependency environments are vulnerability-free.
