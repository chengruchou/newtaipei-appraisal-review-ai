# Issue 22 local acceptance and dependency handoff

Current integration note, 2026-09-11: the privacy workflow and its browser/API
integration are included in merged
[PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45),
main `d148422adb18190bada93b8588a4e34d73e3c2e4`. A complete local OCR restoration
success is reported at the integrated head; this does not establish repeatable
restoration or cloud acceptance. The platform blocks and pending work below
describe the original checkpoint. They are preserved rather than relabeled as
new results. [#22](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/22)
retains OCR stability, diagnosis and privacy operations. See
[local validation](local-validation-record.md) and the
[implementation backlog](implementation-backlog.md).

## Historical acceptance checkpoint

This is the pre-commit acceptance snapshot. Dated statements about uncommitted
work and pending commit permission describe that checkpoint. Subsequent commit
packaging does not establish additional runtime acceptance or authorize a push.

Phase 8 closeout, 2026-09-10. Local implementation is available for independent
review; complete runtime and integrated product acceptance remain blocked.
Nothing in this report asserts deployment, independent approval or live AWS use.

## Revision and evidence scope

Working branch: `feat/local-privacy-pipeline`. HEAD, baseline and the local
`origin/main` tracking reference are
`8591bddc76584ad630774f214c7452c397c937d5`. Phase 0 recorded the fetched baseline;
this closeout checks local refs, not current remote-server freshness. Changes
remain in the working tree, with no staged snapshot or outgoing commits.

Host: Windows / Python 3.12.3. Linux is the runtime acceptance baseline. The
user-authorized Windows development compromise permits continuing local work;
it does not convert a platform block into a pass. No global tools, drivers,
keystore, OCR assets or fonts were installed for this closeout.

Evidence uses synthetic fixtures only. Real PDF parsing, raster rebuilding,
independent decoded-object/pixel checks, AEAD and deterministic refill execute
locally. Human, OCR, publisher, SDK/telemetry and Windows ciphertext-storage
doubles are explicitly named in the relevant tests. Their success does not
establish the corresponding production service or recognition accuracy.

## Implemented scope

| Stage | Implemented local behavior | Contract and operational documentation |
| --- | --- | --- |
| 1 | Separate private/public models, strict validation and pure admission guards | [Contracts](privacy-contracts.md), ADR 0032 |
| 2 | Confined immutable acquisition, isolated parser/rendering, configured OCR port, conservative candidates and explicit blocked pages | [Scan runbook](local-privacy-runbook.md), ADR 0033 |
| 3 | Versioned list/add/edit/remove/preview/page-review/confirm SDK and ephemeral human authority | [Review handoff](local-privacy-review.md), ADR 0034 |
| 4 | Canonical raster PDF rebuild, independent structure/pixel checks and mandatory output OCR verification | [Sanitization](local-privacy-sanitization.md), ADR 0035 |
| 5 | AES-GCM/HKDF mapping encryption, session key lifecycle and immutable Linux storage adapter | [Mapping](local-privacy-mapping.md), ADR 0036 |
| 6 | Published artifact/plan validation, CJK or approved-crop refill, independent output checks and fresh local file publication | [Refill](local-privacy-refill.md), ADR 0037 |
| 7 | Exact-payload export gate, shared local text preparation, Python network denial, Linux namespace runner and value-free leak report schema | [Export](local-privacy-export.md), ADR 0038 |
| 8 | Current regression results, requirement matrix, risk/owner handoff, architecture/traceability updates and unpublished review draft | This document |

Schemas and synthetic JSON examples are versioned together. Existing public
privacy-v1 and service-v1 projections remain separate. No new business-rule
arithmetic, AWS uploader, DynamoDB/SQS/Runtime implementation or browser frontend
is introduced. The existing review/writer completion authority is unchanged.

## Required test matrix

| Area | Local evidence | Remaining limit or acceptance |
| --- | --- | --- |
| Input | `test_privacy_scan.py`: real native/scanned/mixed synthetic PDFs, rotation/CropBox, malformed/encrypted/resource/path/timeout rejection | Native processing works; scanned/mixed pages explicitly block without real OCR. Linux no-follow acquisition test remains unexecuted here. |
| Detection | Same file: format/label candidates, multiline projection, preserved confidence, missing/low-confidence OCR, zero-match review requirement | Regex and OCR doubles do not establish real Chinese OCR-error tolerance or semantic name/entity identity. Visual sensitivity and ambiguous grouping require human review. |
| Confirmation | `test_privacy_review.py`, `test_privacy_contracts.py`: page preview/review, stale source/policy/selection/group, fake actor, expiry/replay and overlapping scan behavior | Real Linux controlling-terminal interaction and #25 UI/authenticated local session are blocked. |
| Redaction | `test_privacy_bundle.py`: actual raster reconstruction, independent reopen, canonical object profile, metadata/attachment/form/hidden content/trailing payload rejection and pixel tampering | Canonical comparison rejects non-profile XMP/incremental/transparency objects, but dedicated fixtures for every PDF encoding variant are not an exhaustive parser-security audit. Real output OCR remains blocked. |
| Geometry | Scan/bundle/refill tests: crop and rotations, fractional dimensions, bounds, token overflow, overlapping refill fields and protected cloud-number pixels | Published refill pages currently require zero rotation and origin-based CropBox. Visual equality is checked at configured DPI, not every zoom/render implementation. |
| Tokens | Contract/refill tests: separate repeated occurrences, exact IDs/entities/fields, malformed or unknown tokens, wrong location, absent token and explicit omission | No live model/publisher output or cross-page consumer integration was exercised. Same-name identity remains an explicit human decision. |
| Crypto | `test_privacy_mapping.py`: real encrypt/decrypt, key/header/ciphertext/nonce/context tamper, case mismatch, expiry, random nonce/subkey domains and repeat-seal conflict | Production provisioner, restart/unlock recovery, Linux permissions/atomic writes/deletion and rollback threat acceptance remain blocked. Random nonce tests are not proof under a compromised RNG. |
| Refill | `test_privacy_refill.py`: real new PDF, embedded CJK font, extraction, approved source crops/omission, preserved cloud rate/total, stale or tampered artifacts and source replacement | Formal #26 template/font assets, real #28 authenticated publisher and actual OCR are blocked. Visual signature crops never create/preserve a digital signature. |
| Egress | `test_privacy_export.py`, `test_privacy_export_network.py`: exact confirmed payload, revocation/tampering rejection, fixed filename/errors, source-fingerprint rejection, text review and canary controls across bytes/streams/text/images/channels | Full core Python denial blocks Windows asyncio loopback. Linux namespace/native executable and live AWS egress acceptance remain blocked. Image template probes and synthetic senders are limited fixtures. |
| Compatibility | Full existing pytest, mypy, local/mocked cloud tests and unchanged existing service/PDF schemas | Windows POSIX/IPC/path failures are retained and compared by exact node ID. No current-head Linux CI or live HTTP/AWS deployment result is claimed. |

The matrix distinguishes implemented rejection paths from unperformed acceptance.
There is no waiver allowing partially scanned or uncertain output to be exported.

## Remaining risks and dependency handoff

| Owner / dependency | Required concrete input or integration | Exit evidence before acceptance |
| --- | --- | --- |
| Local runtime | Approved Linux environment, private source/store directories, existing namespace capability | Run ordinary CI commands, all Linux-only cases and the core namespace runner without platform blocks; verify ownership, race and process-tree denial. |
| OCR / asset owner | Approved local executable, `eng` and `chi_tra` model hashes, permissions and redistributable test provenance | Actual native/scanned/mixed OCR plus output OCR on controlled PDFs; missing or unreliable results still block. No automatic download. |
| Key provisioning owner | Approved local provision/unlock mechanism and retention/recovery policy | Encrypted store survives restart and unlocks with correct context; wrong/missing/expired keys fail; plaintext/key colocation is absent. |
| #25 frontend | Trusted local-session UI, full-page preview/review and exact-export confirmation adapters | End-to-end stale response, actor spoofing, page coverage, unknown free text and exact displayed/output snapshot tests; local data never sent to browser telemetry/cloud. |
| #24 reviewer response | Adopt shared `sanitize_reviewer_text` and final exact-output human review | Known values/fingerprints do not return in text; newly entered or encoded private facts are denied or corrected locally by the reviewer. |
| #26 writer/template | Versioned approved output field/occurrence IDs, geometry, font/license and supported operations | Formal CJK/crop/overflow and protected-calculation goldens match the exact template digest. Reflow does not reuse original coordinates. |
| #28 publisher | Authenticated current case/run/revision/template/artifact descriptor and bytes, live `permits` adapter | Wrong case/run/revision/template, stale publication and artifact replacement fail before local refill. A supplied digest is never publisher authority. |
| #27 transfer / local coordinator | Consume only gate-owned payloads and persist a mapping for that exact confirmed manifest before network handoff | The exported occurrence IDs and decrypted mapping agree across the full lifecycle; key/storage failure prevents transfer. No arbitrary-path or final-PDF upload route. |
| #31 AWS integration | Independently authorized deployment/account and real SDK/HTTP/telemetry composition | Actual egress/metadata/log/invoke/publish inspection with approved canaries, network isolation and no original/final/map/key upload; core offline tests alone are insufficient. |
| Independent reviewer | Review complete working diff, contracts, fixtures, trust assumptions and unresolved rows | Recorded independent review; commit, push and any PR operation each need separate explicit permission. |

The current components do not include a durable end-to-end coordinator. In
particular, `builder.build` issues fresh occurrence IDs: creating a map from one
bundle and then exporting a rebuilt bundle is invalid. Mapping persistence must
use the manifest actually seen by the gate's confirmation/sink and complete before
future network handoff. This composition and recovery behavior remains an explicit
handoff, not a completed cross-stage integration test.

Signed-PDF cryptographic validation is not implemented. Visual signature
restoration must not be described as digital signing.

The operating account and injected adapters are trusted. Python audit hooks cannot
contain native code, pre-existing descriptors or interpreter startup. Raster
rebuilding trades selectable cloud text for bounded visual preservation. Free-text
replacement does not detect every newly entered private fact. Persistent rollback,
same-owner compromise and secure memory/disk erasure are not guaranteed.

## Final delivery checklist

- [x] Functional branch and local baseline identified; no unauthorized commit or remote write.
- [x] English contracts, ports, ADRs, schemas, synthetic fixtures and stage runbooks exist.
- [x] Real synthetic PDF processing and explicit unsupported/missing-OCR rejection are demonstrated.
- [x] Approval invalidation, source preservation, structural/pixel rejection and local-only final output are tested.
- [x] Real AEAD, deterministic refill and exact-payload gate behavior have focused local evidence.
- [x] Remaining platform, asset, UI, publisher and cloud dependencies are individually recorded.
- [ ] Actual OCR, Linux storage/terminal/namespace and restart/key-provisioning acceptance.
- [ ] Durable exact-export mapping/transfer coordination and formal #25/#26/#28 consumer integration.
- [ ] #31 live AWS zero-egress acceptance for original/private/final data.
- [ ] Independent review and separately authorized commit/push/PR publication.

## Reproduction and current validation

Use the already approved repository-local environment. Set TEMP/TMP, coverage,
pytest basetemp and cache paths under ignored `artifacts/issue-22-p8/`.
The existing CI installs `.[dev]`, which already includes the optional privacy
cryptography dependency; no CI check or warning/coverage setting is weakened.
The namespace runner is an additional acceptance command, not a claimed CI result.

```bash
ruff check .
ruff format --check .
mypy src
pytest --cov=appraisal_review --cov-config=pyproject.toml --cov-report=term-missing
python -m pytest cloud_tests
cfn-lint cloud_tests/image-stack.json cloud_tests/runtime-stack.json
python scripts/privacy_offline_tests.py
python scripts/check_submission.py --base origin/main
```

On Windows, `mypy src --platform linux` is additional static analysis only.
The final table below records observed execution rather than extrapolating older
stage counts. Stage 0-7 documents and ignored logs retain their historical results.

| Check | Phase 8 observed result |
| --- | --- |
| All privacy unit tests | 321 passed, 10 skipped; 112.02 seconds |
| Full repository regression with coverage | 893 passed, 40 failed, 43 errors, 10 skipped, 2 warnings; 193.83 seconds; 75% aggregate coverage |
| Exact failure-node comparison to Phase 7 | Same 83 failed/error node IDs; no additions or removals |
| Ruff lint / format | Passed; 228 Python files already formatted |
| `mypy src --platform linux` | Passed; 105 source files; analysis only |
| Native Windows `mypy src` | Three unchanged POSIX API errors in `adapters/local/approval.py` |
| Local/mocked cloud tests | 8 passed; no live AWS or model calls |
| Export subset under Python denial | 55 passed, 1 Linux-only skip, zero failures/errors/platform blocks; scope is explicitly the export subset |
| Linux namespace runner | Exit 2 / blocked on Windows; full core namespace execution not performed |
| Real PDF scan smoke without OCR configuration | Exit 2; native needs review, scanned/mixed blocked with OCR unavailable; all three originals unchanged |
| Real raster sanitizer smoke without OCR configuration | Exit 2; decoded structure/pixels verified, original unchanged, no verified bundle emitted |
| CloudFormation lint | Not run; `cfn-lint` and its local Python package are unavailable; no global installation attempted |
| Existing compatibility files | Seven selected business/authority/schema/CI/instruction files match HEAD; existing core code and workflow were not edited |
| Documentation links | Updated documents' relative file targets resolve; historical remote links were not revalidated |
| Submission/branch/publication draft checks | Passed for the working tree and unpublished PR body; configured Git identity, zero outgoing commits |

The ten skips are eight Linux mapping filesystem cases, one Linux acquisition
case and one Linux anonymous-socketpair case. They are unexecuted acceptance,
not passes. Existing failures retain Windows POSIX/path and restricted IPC
conditions; the exact node comparison is the regression evidence, not a substitute
for Linux execution. The historical Phase 7 full-core Python-denial run also
remains blocked by Windows asyncio loopback; the successful export subset does
not replace it. The offline runner owns its ignored `issue-22-p7/offline` sandbox;
this closeout's aggregate summaries are retained under `issue-22-p8`.

Command outputs, coverage data, failure comparison, compatibility/link checks and
synthetic smoke artifacts are retained locally under `artifacts/issue-22-p8/`.
The full pytest and privacy logs were collected by shell wrappers that tail their
output; their child exit codes were not separately persisted. The non-passing
full test summary is reported explicitly above rather than treating wrapper success
as test success. Smoke exit codes were separately captured. No raw synthetic test
logs, generated PDFs, OCR dumps, key material or real cases are submission candidates.

`artifacts/issue-22-p8/pr-body.md` is an unpublished review draft, not a GitHub
mutation. The supplied untracked instruction plan remains untouched and is not a
submission candidate. Independent review, fresh remote/head CI and commit/push/PR
permissions remain future gates. Local implementation and review preparation are
complete; end-to-end runtime/product acceptance is not.
