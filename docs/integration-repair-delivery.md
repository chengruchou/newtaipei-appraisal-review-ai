# Review repair delivery and integration evidence

This is the dated 2026-09-11 component repair record. Current integration acceptance
is reported in [project progress](project-progress.md). Counts below belong to the
specified component trees and are not summed or presented as the integration suite.
All original pull requests remain open for independent review. No approval, merge,
Issue mutation, branch deletion, history rewrite or live AWS/model operation occurred.

## Original PR repairs

| PR | Finding and repair | Source files (under `src/appraisal_review/` unless stated) | Scoped regression evidence | Latest full head | CI |
| --- | --- | --- | --- | --- | --- |
| [#34](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/34) | PDF alias/inode protection; immutable measured/embedded font buffer; literal-True multi-context delegation | `adapters/local/placeholder_backfill.py`, `pdf_preflight.py`, `pdf_render.py`, `adapters/aws/pdf/s3_pdf_writer.py` | 10 failures before; 21 focused and 686 repo tests after | `c8f739ff936b772200dda9d8e09d18049df03cfc` | [Run 34531027934](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34531027934): failure |
| [#35](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/35) | Restore ignored source package; current attempt/lease/fence/grant transaction; immutable versions and reauthorized download | `adapters/aws/artifacts/`, `adapters/local/artifact_publication.py`, `.gitignore` | Original import failed; clean snapshot 705 tests; installed wheel 36 publication tests | `0971b9d8e6e7472c957bb2e0e236c4d2ff8634ae` | [Run 34531028880](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34531028880): failure |
| [#36](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/36) | Quarantine invalid side-effect receipts; persist run budgets/failures; separate internal task service | `application/controlled_workflow.py`, `bounded_workflow.py`, `workflow_tasks.py`, local workflow ledger | 5 initial counterexamples; 1468 repo tests after main synchronization | `f3bb23b313ee90e585988b1d43e8a5da06d27dc1` | [Run 34532638821](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34532638821): failure |
| [#37](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/37) | Preserve unchanged raw evidence; one exact export bundle with authenticated mapping readback | `application/privacy_review.py`, local privacy export/mapping adapters | Initial 15 privacy and 8 mapping failures; 1524 passed, 10 platform skips after synchronization | `c9717920d782fd3524c81094dec7894a3dc46ac7` | [Run 34532640173](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34532640173): failure |
| [#38](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/38) | Record actual corrected values; rejection projection; owned empty jobs; idempotent seeding; typed subject endpoint | `application/human_tasks.py`, local human task store, task contracts/routes | 6 initial regressions; 1129 repo tests and 50 focused subject/fixture checks | `d9bd475e7d41ee32438d3984d2f7fe91dbf32d1f` | [Run 34531029044](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34531029044): failure |
| [#39](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/39) | Freeze displayed/submitted/retried commands; authoritative units; body deadline; PDF source and privacy UI | `web/src/api/client.ts`, `ResponseForm.tsx`, PDF/subject/privacy components | 8 command/transport and 6 unit failures before; 66 frontend tests on original PR head | `34c047b518cbe44d4d17fe6b0457cc0ad82840e6` | [Run 34532641119](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34532641119): failure |
| [#42](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/42) | Retain independently known input/output cost without replacing unknown totals | `cloud_tests/extraction_smoke.py` and regressions | 4 initial cost failures; 185 focused tests | `d04c81ef9a3c9532b62b3f717c58ff233fa39c7f` | [Run 34531029788](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34531029788): failure |
| [#43](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/43) | Check deadline after wait and after heartbeat, preserving cancellation/lease priority | `application/runtime_worker.py` and regressions | 39 worker/SDK/emulated storage tests; late cases produce no result object | `c10e79a46762bc457d6d2a681b7b7fc5182fbc9f` | [Run 34532640425](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34532640425): failure |
| [#44](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/44) | Preserve strict evidence gates and consume upstream repairs; no invented finding | Stack synchronization from #43 | No new defect claimed; prior CLI evidence remains scoped | `9ef730b3961182cb92923e82b40c8c0dc3f7ed10` | [Run 34532641649](https://github.com/chengruchou/newtaipei-appraisal-review-ai/actions/runs/34532641649): failure |

The #34 run annotation states that jobs were not started because of account
payment/spending-limit configuration. Other fetched failing runs expose no executed
steps; no test success is inferred. No billing setting was changed and no remote
rerun was requested. Head-specific local evidence is not a substitute for remote CI.

The stacked bases remain #39 → #38 and #42 → #43 → #44. Normal merge commits retain
the original histories. The integration branch joins those exact reviewed heads;
a local Git merge is not a GitHub PR approval or merge.

## Original review replies

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

