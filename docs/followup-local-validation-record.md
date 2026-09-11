# Follow-up local validation record

Recorded 2026-09-12. This record describes the executable checkpoint
`add3c0e23dc978dff082450d6475ace1e599261c` on
`feat/local-validation-stack`, following [#52](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/52)
and [#53](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/53).
The publication commit adds only documentation. Runtime, tests, schemas, locks,
README package metadata and image build inputs remain byte-identical to this
checkpoint; the complete suite was not rerun under the later documentation SHA.
The final PR records its actual published head and separate hosted CI result.

## Implemented follow-ups

- ADR 0048 binds each requested model to its own exact routing proof. The
  extraction preflight and every physical SDK send/retry preserve that binding;
  another model's destinations or a new client cannot revive invalid authority.
- ADR 0049 adds finite, request-local restoration diagnostics without exposing
  paths, OCR text or secrets, changing existing HTTP bodies/status, or removing
  either authority check around reading. The browser retains low-confidence
  measurements and uses separate positive, automatic, paused and resume scenarios.
- ADR 0050 adds the explicit synthetic Linux ARM64 container, bounded local
  server, durable state, local Docker endpoint/builder checks and browser wrapper.
  The separate native host companion serves the exact image-exported UI; original
  documents, mapping keys, OCR and revealed PDFs stay on the host.

The [competition traceability](competition-requirements.md) and
[delivery traceability](delivery-traceability.md) retain the merged implementation
and official source bindings. Earlier PDF/font/version-retention, workflow trace,
gateway recovery, exact mapping and originating-field repairs were already in the
merged baseline; these follow-ups preserve them rather than repeating their PRs.

## Exact-checkpoint local evidence

| Check | Observed result |
| --- | --- |
| Repository Python suite | 3,307 passed, 10 platform-specific skips, 88% coverage; Python 3.13.5 |
| Local cloud suite | 12 passed; local/emulated/injected scope, no AWS requests |
| Frontend | 230 tests in 27 files; type, lint, format, build and artifact check passed; the artifact check inspected five built files |
| Quality and contracts | Ruff, format, mypy, dependency integrity, goldens, ten exporters/82 files and OpenAPI passed without generated drift |
| Actual HTTP | Legacy/unconfigured boundaries and configured service/invocation/PDF smoke passed |
| Infrastructure | Six CloudFormation templates and the offline rehearsal plan passed |
| Installed wheel | 101 non-editable installed-package regressions and actual configured HTTP/PDF execution passed; all 212 package payload files match source |
| Actual image core | Four Chromium core scenarios and lifecycle checks passed: real PDF download/reopen, unauthorized rejection, receipt replay, restart and stop/start persistence |
| Source protection | All 775 checkpoint files and the original workspace's 18 saved files unchanged after full verification; four protected files unchanged |

The six captured Python warnings are upstream SWIG and Starlette/AnyIO
deprecations. No SQLite ResourceWarning was observed. No global warning
suppression was added. The source environment covers the declared
base/dev/privacy/documents/pdf groups; the separate AWS Lambda entry is not
accepted merely because its dependency is installed in the image.

Wheel SHA-256:
`d4f9ab93a76e80af79cdef6a695f29359fea68cfc4e79c30035a71b96a0477c8`.

Image ID:
`sha256:5f1a30625a4c3333a4f9cc1c01a19df8163632de71c6bb77445c530f88b00847`.

The image runs Python 3.12.14 as UID/GID 10001 on Linux ARM64. All 210 Python
modules, both competition catalogs and all 32 runtime dependency pins were
verified from the actual image. Core browser execution used the locked Playwright
1.63.0 Chromium build 1243. Testing used a macOS ARM64 host and Docker Desktop;
other host/platform combinations remain unaccepted. Disabled bridge IP
masquerading is not complete outbound network isolation.

## Original synthetic privacy cases

Both completed cases use the original failing synthetic input hashes, not
competition attachments or real cases:

- Criteria: `b25996a905fe26f8ab2bb9812f5a0b46f03d5c90f079d90815e9c49abde4bc9f`.
- Forms: `9495c0d00a2e2c33025dd72d40c0da1566cb78b1d2346b3bf2ca88ffbce5145f`.

Both use the same checkpoint, actual image-exported HTML/JS/CSS bytes and native
host companion. The existing 900-second key scope, 0.85 threshold, OCR assets,
144-DPI OCR/refill and 288-DPI sanitization remain unchanged. Every stage retains
all raw observations; four material confidence values remain zero through
explicit current-side confirmation.

The full positive scenario passed in 6.6 minutes. Its separate same-case
automatic-refusal check passed before individual readings, with zero confirmations
and receipts. Fresh inspection covered two full pages and seven published crops,
then two full pages and four candidate crops. Eleven individual UI confirmations
produced two separately bound receipts. All 99 published and 108 candidate OCR
observations remained unchanged.

The separate fresh sequence passed paused, automatic refusal, and resume on the
same live case. Paused evidence was archived before subsequent operations and
contained zero readings/receipts. Resume recaptured the same pages and view;
crop screenshot bytes changed, so all seven current crops were inspected again
before writing a new exact reading plan. The second stage also received fresh
inspection. This is a separate pause/resume result, not a second full positive run.

| Actual browser download | Restored PDF SHA-256 | Final GET |
| --- | --- | --- |
| Full positive | `4ebe0e35030adf17669300ef80b554680762d9f18824699508b8dfc28bcfe4dc` | 200; browser headers 9,302.950 ms, server 9,292.134 ms |
| Paused/automatic/resume | `e3ac4a63076d3665689f3554cd6db9d556c2dab6a0e98cbb9104e7eb6b6c02f3` | 200; browser headers 8,811.882 ms, server 8,801.936 ms |

Each download has an actual request UUID joined to the successful server
diagnostic record. Both PDFs were independently reopened and visually inspected.
Exact source crops, eight appraisal fields, non-restoration pixels, original
sources and placeholder downloads are checked separately. The third restored
crop is the deliberately blank extra browser selection in this authored fixture.
All owned services stopped with normal SIGINT; no old process authority or
receipt was reused for another case. Synthetic visual confirmations do not
constitute real-material, independent human or business approval.

Failed attempts remain preserved: an earlier full-positive attempt failed before
candidate confirmation, and its later candidate reading plan was observed after
the latest possible mapping-key expiry; the later launcher initially lacked a local Playwright
binding; one unused second-case bootstrap was stopped during investigation.
Offline wheel verification initially had an incomplete copied test fixture layout.
The first PDF verifier mistakenly used output coordinates to crop a narrower
source page; its successor uses the exact recorded source regions and keeps all
pixel, receipt and observation assertions. These are explicitly separate harness
corrections and results. Success does not retroactively establish the missing
historical timeout/request correlation or corpus-wide OCR reliability.

## Security, submission and external gates

Fresh source-Python, 33 runtime/build-pin and npm advisory queries found no
vulnerabilities in their recorded scopes. The exact-image OS severity gate failed:
176 findings, including 3 Critical, 51 High, 57 Medium, 57 Low and 8 Unknown.
No finding lists a fixed version in the scanned Debian database. This is not
universal proof that no upstream remedy exists, nor risk acceptance. [#50](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/50)
owns remediation. The scanner used a fresh database and no ignore or severity
reduction.

Credential-pattern scans cover complete source/history/index/working content,
build context, all 12 exported image layers including deleted members, and
execution logs. Raw source/layer reports retain `needs_review`; exact fixed
synthetic fixtures and public wheel members were individually adjudicated.
No incomplete input, runtime secret canary or private material path was found
in the image. A heuristic credential check is distinct from vulnerability scanning.

The unchanged submission gate covers every outgoing full commit tree and
metadata, index, working files, functional branches and actual publication text.
Existing user Git identity and required license notices are preserved.
No Issue mutation, pull-request approval/merge, history rewrite, CI rerun,
billing change, AWS operation or real material approval is part of this delivery.

The exact heads of #52 and #53 triggered
[run 34621115958](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34621115958)
and [run 34624646747](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34624646747).
Both had payment/spending-limit annotations and no test steps or artifacts.
The final stack PR's newly published head must be checked separately; local
results and historical CI are not substitutes.

Draft status remains required for independent review, hosted CI, OS remediation,
supported-platform and repeated-corpus acceptance, production onboarding/identity
and cloud persistence, designated model quality, official assets, and authorized
competition/cloud deployment. See the [implementation backlog](implementation-backlog.md).
Reproduce the local topology with the [stack runbook](local-validation-stack.md)
and the [strict privacy scenario runbook](../web/docs/privacy-browser-scenarios.md).
