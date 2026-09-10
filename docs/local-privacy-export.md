# Local export gate and offline regression

Phase 7 implements the owned-bundle export operation, shared local reviewer-text
preparation, Python worker network denial and synthetic leak regression tooling.
The sink is a trusted port; tests use local doubles and send nothing to the cloud.
Production human export confirmation, actual OCR, Linux namespace acceptance and
#31 cloud integration remain pending.

## Local SDK handoff

Compose `LocalPrivacyExportGate` with the same `LocalSanitizedBundleBuilder`,
`LocalSanitizedVerifier` and live `PrivacyApprovalAuthority` used for the reviewed
source. Inject a trusted `PrivacyExportConfirmation` and `PrivacyExportSink`.
Consumers cannot supply those adapters through request data.

```python
manifest = gate.export(view.command, approval, reviewer_text=local_response)
```

The gate builds and verifies the PDF internally, validates its digest/size and
serializes the exact public manifest type. The confirmation adapter must locally
show the PDF and all optional reviewer text and obtain explicit human consent.
An earlier source approval or a generic caller-provided boolean is insufficient.
Immediately after confirmation the gate rechecks the same payload and live
approval/verifier state. The sink gets that exact immutable payload instance.
Changed bytes, manifest JSON, text, filename, revoked approval or replaced verifier
state prevent the sink call. Calls on one gate are serialized with a local lock.

`PrivacyExportPayload` has only `pdf`, `manifest_json`, `reviewer_text` and fixed
`sanitized.pdf` filename. Its construction alone grants no authority. The gate
accepts no arbitrary path/PDF, original filename, caller metadata, final refill,
mapping, private error or serialized export grant. A sink must consume those
bytes directly and must not attach source paths, local context or extra metadata.
It must not expose a parallel consumer-facing upload operation. Existing upload
adapters remain outside this boundary and are not approved for sensitive cases.

Adapter failures become fixed `PrivacyFault` problems without raw exception
messages in the public error channel. Do not serialize exception internals or
locals. A sink failure is not automatically retried; a future network adapter
must define partial-delivery reconciliation separately. The last approval check
is the local handoff point, not a distributed transaction with a future service.
The returned manifest does not advance business status to `completed`.

## Reviewer text for #24/#25

Both consumers can call `sanitize_reviewer_text(text, command)` locally to obtain
a `LocalTextDraft`. It always says `needs_review` and hides its text from repr.
The gate calls this same function itself; it never trusts a consumer-authored
draft as an approval. Known redacted values become their existing random entity
tokens, including case, width and whitespace variants. NFKC normalization is
visible in the draft. Conflicting entity assignments, remaining known values,
unsupported controls, oversized text and excessive replacement data fail closed.
The original source digest is also rejected in this text channel, including
case/whitespace variants; human confirmation cannot authorize that fingerprint.

This helper is not a general detector of newly typed personal information.
Unknown names, paraphrases and encoded values may remain and require explicit
human review of the final text. Denial exports nothing; edit locally and repeat
the gate. Do not upload drafts or place them in public logs. Filenames and PDF
metadata use fixed/allowlisted output rather than this free-text channel.

## Offline execution

`run_bounded` launches the scan, sanitizer and refill Python workers through
`offline_worker`. Its audit hook rejects Python socket/DNS events before target
imports, including Python model loaders that attempt downloads. Existing bounded
pipe transport still suppresses raw stderr and avoids inherited credentials.
Anonymous AF_UNIX socketpairs remain available for Linux asyncio; explicit
bind/connect and DNS operations remain denied. Windows asyncio implements its
internal socketpair using TCP loopback, which this guard intentionally blocks.
This is a platform limitation, not an exception allowing loopback networking.
Direct execution of an individual worker module bypasses the wrapper and is not
the supported adapter entrypoint.

Python hooks are not an OS sandbox. They do not protect native networking,
external OCR executables, inherited file descriptors or interpreter startup
hooks. Production composition must enforce OS isolation independently. No OS,
global firewall, tool, driver or model configuration is changed by this phase.

```bash
# Cross-platform Python denial regression; not Linux acceptance:
python scripts/privacy_offline_tests.py --python-hooks

# Export-only regression without the Windows asyncio-dependent scan/review suite:
python scripts/privacy_offline_tests.py --python-hooks --suite export

# Linux acceptance: requires existing unshare and permitted user namespaces:
python scripts/privacy_offline_tests.py
```

The Linux runner creates a fresh user/network namespace, checks a native libc
UDP connect returns `ENETUNREACH`, and verifies a child inherits the namespace.
The full privacy test process tree, including workers and executable fixtures,
runs inside it. It fails explicitly when this capability is unavailable; it
never silently falls back to Python-only tests. Real OCR/model assets are still
required for their separate acceptance, even when namespace tests pass.

The runner suppresses pytest tracebacks/captured output/node IDs and emits only
scope, outcome and aggregate counts. Keep diagnostic pytest runs local, under
ignored artifacts; they are not the value-free report surface. The runner uses
repository-local temporary/cache directories and disables third-party pytest
plugin autoload to keep the exercised runtime explicit.

## Leak evidence and limits

`tests/unit/privacy_export_support.py` defines synthetic canaries by IDs `c01`,
`c02`, `c03`. Their values stay in the local test corpus, not the report. The
regression scanner checks actual PDF bytes, decoded xref dictionaries/streams,
extracted text and rendered image templates. Positive controls prove detection
of compressed content and scanned images with no text layer. Template matching
uses known synthetic glyph pixels at 144 DPI; it is not a general OCR detector.
The production sanitizer still requires independent local OCR and canonical
structure/pixel checks described in [sanitization](local-privacy-sanitization.md).

Tests capture stdout, stderr, logs, exception formatting and serializers. Network
probes cover sockets, DNS, urllib and httpx, plus explicitly synthetic SDK and
telemetry senders and an import-time downloader. Injection controls intercept
httpx before transport and prove canary hits while the export sink stays untouched.
No installed cloud SDK or telemetry vendor integration is claimed by these doubles.

`schemas/privacy-leak-report-v1.json` allows only fixed surface/canary enums,
counts, scope and status. Missing or uninspected surfaces require `blocked`;
any hit requires `failed`. A clean PDF/channel subset does not assert complete
network coverage. Reports are evidence descriptions and cannot authorize export.
Regenerate the explicitly synthetic examples with:

```bash
python scripts/export_privacy_export_contracts.py
```

See [example descriptions](../examples/privacy-export-v1/README.md). Core offline
results do not prove AWS zero-egress. #31 must inspect actual HTTP/SDK/telemetry
composition and deployment isolation; #27 must consume only gate-owned payloads;
#24/#25 must implement exact-output human confirmation before production use.

## Development validation, 2026-09-10

Windows / Python 3.12.3, local uncommitted work on `feat/local-privacy-pipeline`
based on `8591bddc76584ad630774f214c7452c397c937d5`.

| Check | Observed result |
| --- | --- |
| Final Phase 7 focused tests | 55 passed, 1 Linux-only test skipped; real PDF workers, local sink and explicit human/OCR/SDK/telemetry doubles |
| Final export suite under Python denial | 55 passed, 1 skipped; no failed/error cases, no platform blocks in this subset |
| Full regression with existing coverage settings | 884 passed, 40 failed, 43 errors, 10 skipped, 2 warnings; 75% aggregate coverage |
| Failure comparison with Phase 6 | Exact same 83 failed/error node IDs, no additions or removals |
| Ruff lint and format | Passed |
| Linux-target mypy | Passed, 105 source files; static analysis only |
| Native Windows mypy | Same three pre-existing POSIX API errors in the business approval adapter |
| Local/mocked cloud tests | 8 passed; no live AWS |
| Linux namespace execution | Blocked on Windows; no Linux runtime acceptance claimed |
| Full core under Python denial | 251 passed, 14 failed, 47 errors, 10 skipped; all 61 failures/errors identify Windows asyncio socketpair denial, so outcome is blocked |
| Submission, branch and whitespace checks | Passed; zero outgoing commits and configured Git identity |

Ignored test logs and offline summary are under `artifacts/issue-22-p7/`.
The full regression and core-denial runs preceded the final source-fingerprint
guard and nine additional negative checks (eight report checks and that guard).
All affected export tests then passed both normally and under Python denial;
those final focused runs contain 55 passes. The full-run counts above are the
observed earlier run, not an extrapolated total. No coverage exclusions were added.
The additional Linux-only anonymous-socketpair test accounts for the tenth skip.
No existing platform tests or network denial checks were weakened. The offline
runner counts platform blocks explicitly and never reports them as passing.
No originals, final local PDFs, keys or mappings were uploaded. No new dependency,
global tool or font installation was performed. No commit, push or remote mutation
was authorized or performed. Phase 8 remains integration acceptance, documentation
closure and review preparation, with unresolved capabilities recorded honestly.
