# Review repair delivery and integration evidence

This record was consolidated on 2026-09-11 after
[PR #45](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45)
merged into `main` as `d148422adb18190bada93b8588a4e34d73e3c2e4`, with the same
tracked tree as reviewed head `fd22e68321bad6b58f06068dfef1db67fdb1c269`.
The merged code is the local integration validation baseline. Current acceptance
is reported in [project progress](project-progress.md), exact evidence boundaries
in [the validation record](local-validation-record.md), and remaining work in
[the implementation backlog](implementation-backlog.md).

Component counts below belong to the specified historical trees. They are not
summed or presented as the integrated suite. Source PR lifecycle state is separate
from whether its work reached `main`; the original repair table and discussions
are retained as provenance, rather than instructions to merge the old stacks
again. No live AWS/model acceptance is implied.

GitHub already records #34–#38, #42 and #46 as merged. The latest heads of #39,
#43 and #44 are also ancestors of merged `main`, with no remaining head commits
outside `main`; their redundant stacked PR records are closed as incorporated
through #45. That disposition is distinct from a separate GitHub merge
of each PR. The implementation backlog tracks remaining work instead of reopening
the historical stacks.

## Repairs included in the merged baseline

The final round fixes the additional findings raised after the original repair
table. Normal merge commits preserve the component histories and dependency order
#34 → #35, #38 → #39 and #42 → #43 → #44.

| Finding | Merged correction | Source commit |
| --- | --- | --- |
| #34 trusted font approval | New PDF paths reject absent trusted font approval and validate/render the same immutable font bytes; legacy scope remains | `8c29f9d038c05bc4eee91e33d5b0b7e7bac4d8df` |
| #35 PDF synchronization | Retains alias/inode protection, immutable font bytes and literal-True capability checks, including installed-wheel behavior | `eb834038c607cac19f4c4ae9365e0e9fad6ea57a` |
| #35/#45 pinned object versions | Removes blanket noncurrent-version expiration so a newer version does not invalidate the manifest's pinned older version | `1de33cc0228563ccd049a54e7e355fc3f4ebfbae` |
| #36 direct-to-bounded handoff | Retains causal history, consumed budget, open tasks and terminal replay for the reproduced direct handoff | `17679772ab2fac162f0c519fe23d02e3e473afef` |
| #39/#45 uncertain gateway outcome | Preserves frozen command/key after noncanonical 502/504 or body loss; real committed-before-failure recovery yields one receipt without another revision | `774ff6cba719e0235d95a103972f80293d46fdf3` |
| #45 exact restoration mapping | Selects the successful manifest's exact mapping without turning unrelated retained failed exports into authority dependencies; invalid selected mappings still fail closed | `3bcf43ac0207ea0ab50e05c0a792cc50fded5d15` |
| #46 originating field diagnostics | API/UI expose original missing-evidence field IDs while retaining whole-case fill denial, calculated values and raw confidence; generated contracts agree | `3bcf43ac0207ea0ab50e05c0a792cc50fded5d15` |

The same integration adds versioned competition data admission, persistent shared
physical-dispatch limiting and aggregate budgets, exact profile preflight, and
two-stage local OCR review. Canonical ADRs 0024, 0028 and 0035 remain unique and
all 47 ADRs are indexed. Commit
`754e7059105ea762ac7c12cd600600666a50fffe` repairs CDK TLS-policy recognition,
scanner coverage and validation issues. Final commit
`fd22e68321bad6b58f06068dfef1db67fdb1c269` only adjusts the Moto budget test
transport to emulate atomic UpdateItem; production concurrency is unchanged.

The author reports 3,223 Python tests, a separate 12 cloud tests and 150 frontend
tests passed, plus real Chromium core 7/7, gateway recovery 1/1, automatic OCR
refusal 1/1 and complete original privacy restoration 1/1. Independent merge
review passed separate core 289, competition/admission/export 273 and privacy 83
focused scopes. Its supplemental frontend 150 used an older dependency environment
and its privacy environment used PyMuPDF below the declared minimum; Moto/CDK and
full browser OCR were not independently repeated. These scopes are not added or
substituted for the author's full declared-environment execution.

The restored PDF SHA-256 is
`2489bae87b1ea91d79435b27aecab19c2e5e632deff397f43b82492cd4f54982`.
One reported complete success supersedes the old statement that no restoration
had succeeded; it does not erase earlier HTTP 409/timeout failures or establish
repeated-run reliability. See the validation record for the 99/108 retained OCR
observations, eleven visual confirmations, two exact receipts, retained confidence
zero, and current package/image digests.

The [merge review](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45#pullrequestreview-5176654554)
accepted the requested local baseline while retaining the
[P2 exact per-model routing/preflight finding](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/45#discussion_r3987437471)
for repair before cloud enablement. CI, 174 OS image findings (including 3 Critical
and 51 High), OCR repeatability, configured deployment and formal/model acceptance
remain open. Fresh dependency advisory results are scoped separately from the
unresolved image scan. The default AWS Docker entry point is still unconfigured
and returns 503.

The post-merge
[main CI run 34580935446](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34580935446)
failed with empty Python and web step lists and zero artifacts. Its precise cause
has not been verified. This is separate from the earlier head CI failure and is
not a hosted test pass.

## Historical original PR repairs

| PR | Finding and repair | Source files (under `src/appraisal_review/` unless stated) | Scoped regression evidence | Head at this historical repair round | CI at that head |
| --- | --- | --- | --- | --- | --- |
| [#34](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/34) | PDF alias/inode protection; immutable measured/embedded font buffer; literal-True multi-context delegation | `adapters/local/placeholder_backfill.py`, `adapters/local/pdf_preflight.py`, `adapters/local/pdf_render.py`, `adapters/aws/pdf/s3_pdf_writer.py` | 10 failures before; 21 focused and 686 repo tests after | `c8f739ff936b772200dda9d8e09d18049df03cfc` | [Run 34531027934](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34531027934): failure |
| [#35](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/35) | Restore ignored source package; current attempt/lease/fence/grant transaction; immutable versions and reauthorized download | `adapters/aws/artifacts/`, `adapters/local/artifact_publication.py`, `.gitignore` | Original import failed; clean snapshot 705 tests; installed wheel 36 publication tests | `0971b9d8e6e7472c957bb2e0e236c4d2ff8634ae` | [Run 34531028880](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34531028880): failure |
| [#36](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/36) | Quarantine invalid side-effect receipts; persist run budgets/failures; separate internal task service | `application/controlled_workflow.py`, `application/bounded_workflow.py`, `application/workflow_tasks.py`, `adapters/local/workflow_run_ledger.py` | 5 initial counterexamples; 1468 repo tests after main synchronization | `f3bb23b313ee90e585988b1d43e8a5da06d27dc1` | [Run 34532638821](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34532638821): failure |
| [#37](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/37) | Preserve unchanged raw evidence; one exact export bundle with authenticated mapping readback | `application/privacy_review.py`, local privacy export/mapping adapters | Initial 15 privacy and 8 mapping failures; 1524 passed, 10 platform skips after synchronization | `c9717920d782fd3524c81094dec7894a3dc46ac7` | [Run 34532640173](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34532640173): failure |
| [#38](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/38) | Record actual corrected values; rejection projection; owned empty jobs; idempotent seeding; typed subject endpoint | `application/human_tasks.py`, local human task store, task contracts/routes | 6 initial regressions; 1129 repo tests and 50 focused subject/fixture checks | `d9bd475e7d41ee32438d3984d2f7fe91dbf32d1f` | [Run 34531029044](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34531029044): failure |
| [#39](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/39) | Freeze displayed/submitted/retried commands; authoritative units; body deadline; PDF source and all-page exact privacy preview | `web/src/api/client.ts`, `web/src/features/ResponseForm.tsx`, `web/src/privacy/PdfPreview.tsx`, subject/privacy components | 8 command/transport and 6 unit failures before; 68 frontend tests on original PR head; actual native-preview failure repaired with all-page PDF.js gate | `02e2b438a165a4e48f9147296936356eb57212e9` | [Run 34538884098](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34538884098): failure |
| [#42](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/42) | Retain independently known input/output cost without replacing unknown totals | `cloud_tests/extraction_smoke.py` and regressions | 4 initial cost failures; 185 focused tests | `d04c81ef9a3c9532b62b3f717c58ff233fa39c7f` | [Run 34531029788](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34531029788): failure |
| [#43](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/43) | Check deadline after wait/heartbeat and preserve cancellation/lease priority; give UID 10001 a non-login POSIX identity | `application/runtime_worker.py`, `infra/runtime/Dockerfile` and regressions | 39 worker/SDK/emulated storage tests; late cases produce no result object; additional UID regression failed before fix, 30 infrastructure tests pass | `9ced7f98e510e20c7bd2fecea3a051a291e78b4a` | [Run 34536602618](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34536602618): failure |
| [#44](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/44) | Preserve strict evidence gates and consume upstream repairs; no invented finding | Stack synchronization from #43 | No new defect claimed; prior CLI evidence remains scoped | `6e383da04e3756c104b13ffe43d0627c87f192d0` | [Run 34536742560](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34536742560): failure |

The #34 run annotation states that jobs were not started because of account
payment/spending-limit configuration. Other fetched failing runs expose no executed
steps; no test success is inferred. No billing setting was changed and no remote
rerun was requested. Head-specific local evidence is not a substitute for remote CI.

At this earlier round the dependency order was #38 → #39 and #42 → #43 → #44.
The listed commits were subsequently joined with the additional repairs above in
PR #45. These earlier local Git merges were distinct from the later GitHub merge
of #45 into `main`.

## Historical original review replies

- [PR #34 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/34#discussion_r3983654700) preserves the original discussion and links the repair.
- [PR #34 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/34#discussion_r3983654879) preserves the original discussion and links the repair.
- [PR #34 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/34#discussion_r3983655062) preserves the original discussion and links the repair.
- [PR #35 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/35#discussion_r3983655233) preserves the original discussion and links the repair.
- [PR #36 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/36#discussion_r3983655413) preserves the original discussion and links the repair.
- [PR #36 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/36#discussion_r3983655613) preserves the original discussion and links the repair.
- [PR #37 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/37#discussion_r3983655806) preserves the original discussion and links the repair.
- [PR #38 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/38#discussion_r3983656016) preserves the original discussion and links the repair.
- [PR #38 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/38#discussion_r3983656224) preserves the original discussion and links the repair.
- [PR #38 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/38#discussion_r3983656415) preserves the original discussion and links the repair.
- [PR #39 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/39#discussion_r3983656633) preserves the original discussion and links the repair.
- [PR #39 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/39#discussion_r3983656851) preserves the original discussion and links the repair.
- [PR #39 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/39#discussion_r3983657014) preserves the original discussion and links the repair.
- [PR #39 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/39#discussion_r3983657156) preserves the original discussion and links the repair.
- [PR #39 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/39#discussion_r3983657326) preserves the original discussion and links the repair.
- [PR #42 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/42#discussion_r3983657513) preserves the original discussion and links the repair.
- [PR #43 review reply](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/43#discussion_r3983719724) preserves the original discussion and links the repair.

All 17 published replies were read back from GitHub. The actual publication bodies
and replies are checked with the unchanged submission checker in addition to full
working/index/outgoing snapshots and Git identity metadata. Review threads are left
for their reviewers to assess; prior reviews do not approve new heads automatically.

## Shared integration

See [canonical contracts and migration](service-contracts.md),
[SQLite review transactions](adr/0030-sqlite-local-review-transactions.md),
[SQLite publication](adr/0040-sqlite-publication-transactions.md),
[local privacy bridge](local-privacy-bridge.md), and
[synthetic fixture authority](integration-fixture.md).

A synchronized migration updates undeployed strict task consumers. The legacy
single-context manifest stays unchanged; artifact-manifest-v2 supplies complete
contexts and fenced publication identity. Source authorization, actual human task
responses and deterministic verification retain separate authority. Raw confidence
zero remains zero. Only clearly authored synthetic assets receive isolated test
authorization; no real material is approved.

## Additional integrated download review

Commit `52bc5e544c419c90b6489a713447f9f3d6bf1f1a` repairs the cached local
restored-PDF endpoint in `adapters/local/privacy_bridge.py`. Six regressions
against archived `851a1032f0c5f2115b4501bf4327619e68158858` reproduced HTTP
200 after revocation, a changed run, or locked mapping access, both before and
during the file read. The endpoint now retains the original result/plan binding
and reauthorizes before and after every cached read. Repeated authorized downloads
do not rerun OCR, write a new PDF, or repeat C2 ingestion. The existing coordinator
and bridge selection passed all 42 focused checks; the explicit OCR double in
these tests does not establish measured OCR restoration acceptance.
