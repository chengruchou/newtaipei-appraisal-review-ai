# Review corrections and functional branch migration

## Refreshed baseline

The working tree was clean. Remote inspection found foundation PR #10 already
merged into main at ff5e9d1d511e015dbc96aa5b380cce1def433421; #11 and #12 were
still open with unchanged heads and successful CI. No target-name collision or
new teammate branch was found. This revision preserves every original commit.

| Original PR | Original base SHA | Original head SHA |
|---|---|---|
| #10 | b69ed74da950f37add016b5e881a7e0478f9c7d5 | e24265d7c4ef0bb5d2d74d0ed94bffa2052a08ed |
| #11 | e24265d7c4ef0bb5d2d74d0ed94bffa2052a08ed | 2e3d265fbc3e64bd843ebb608deb37c49da5ffab |
| #12 | 2e3d265fbc3e64bd843ebb608deb37c49da5ffab | c91230a3974006acced6f754377bf49dadac9e90 |

The old names below are historical references, not permissible new work names.

| Old branch | Functional branch | Active delivery and base |
|---|---|---|
| codex/shared-pdf-contract | feat/shared-pdf-contract | #10 remains merged; exact foundation SHA retained, no empty replacement PR |
| codex/member-a-entrypoint | feat/member-a-entrypoint | [#13](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/13), base main |
| codex/agentcore-cloud-smoke | test/runtime-smoke | [#14](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/14), base feat/member-a-entrypoint |

## Review-to-fix mapping

- [API discussion r3939585201](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/11#discussion_r3939585201):
  5fea6df91fad79be77c285eb46d1cd0816b15fd7. Preserve legacy detail while removing
  raw input/context; publish EntryProblemResponse for review 422/503/500 and
  share its serialization with invocation. Eight new schema/runtime tests plus
  the expanded real HTTP smoke. Success and review semantics are unchanged.
- [Submission discussion r3939585204](https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/11#discussion_r3939585204):
  59f62d4388fd35e4765440a475f63017e789000c. Complete outgoing snapshots, index,
  metadata, working/publication files and branch boundaries; 49 actual CLI tests.
  Narrow policy/test literals are examples, never directory-wide exclusions.

All original review submissions were COMMENTED, not formal approvals. #10/#12
had no inline review threads; #11 had the two unresolved threads above. Keep
those threads and their history, attach fix links, and request new independent
review. Their old feedback does not approve a replacement head. The authenticated
account is the author; do not change identity or attempt to bypass self-approval.

## Verification and retirement gate

A baseline: 76 passing tests and successful lint/types. Revised A: 133 tests.
Runtime adds 8 credential-free tests and CloudFormation lint. Run Ruff, format,
mypy, localhost HTTP smoke, protected-file comparison and full submission gate.
The replacement PRs record their exact latest base/head SHAs and GitHub CI.

Only after both replacements have correct bases, preserve the original heads
as ancestors, and pass CI may #11/#12 be closed as superseded. Recheck remote
tips before removing only the three recorded old work refs. If a tip changed,
preserve it and integrate its new work before retirement. Do not rewrite commits,
force-push, delete comments or merge PRs. #10 stays merged in its original history.

Review order: merged foundation #10, then #13, then #14. B remains #5; full
semantics remain #8. This revision does not build/deploy AWS resources.
