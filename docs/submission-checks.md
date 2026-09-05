# Submission and branch checks

Run the gate from the repository root before every commit, push and publication:

```bash
python scripts/check_submission.py --base origin/main
python scripts/check_submission.py --base origin/main --publication-file artifacts/pr-body.md
python scripts/check_submission.py --base origin/main --head feat/member-a-entrypoint --branch feat/member-a-entrypoint
```

Use the full outgoing range. The gate examines every commit's complete tree and
author/committer/message, plus the selected head, staged blobs, tracked/untracked
non-ignored working files and requested publication file. A clean final tree
does not hide an earlier outgoing violation. Staged content is read from the
index, not substituted with an unstaged working copy. Binary or nested repository
content requires manual inspection and stops the CLI; it is not silently skipped.
Author/committer for the next commit must match the existing user configuration.

Current and explicitly named destination branches are checked. Detached checkouts
must provide --branch. Names use feat/, fix/, test/ or docs/ and contain functional
words. The marker check respects separators, CamelCase and acronym boundaries;
main, domain and maintain remain valid. Old branch names may appear only as
historical migration references; that does not authorize recreating those refs.

The scanner recognizes authorship, coauthor, committer, sign-off, written/generated,
assistance and collaboration credits, common metadata keys and attribution badges
or footers. Matching is case-insensitive and handles common Unicode dashes and
Markdown formatting. Ordinary service/model documentation, human signatures and
required license notices remain legal. This deterministic check supplements human
inspection of equivalent wording and submitted metadata; it is not a semantic
proof for every possible tool name or language.

## Explicit negative examples

Markdown permits only a complete, single-line example in this form:

Prohibited example (must fail): `Signed-off-by: Codex`
Prohibited example (must fail): `Written by Claude`

For Python negative tests, only the string literal directly passed as the sole
argument to prohibited_example(...) is treated as test data. The surrounding
source, docstrings, comments and other literals are still checked. The helper
returns the string so subprocess tests exercise the actual CLI on unlabelled
publication/commit/file content. This is not a general suppression directive.
Regular-expression syntax is code, not an attribution; tests preserve that case.
Unlabelled signatures in docs or tests fail just as they do in application files.

tests/unit/test_submission_cli.py runs the CLI in isolated synthetic Git repos.
It verifies nonzero failures with no success output, legitimate content, index
versus worktree differences, removed/replaced earlier content, commit identities,
publication files, explicit destinations and branch word boundaries.

CI supplies --cov-config=pyproject.toml explicitly. pytest-cov then propagates
the absolute config path to CLI subprocesses running in temporary repositories.
Without it, the parent discovers branch coverage from pyproject.toml while
children in another directory can default to statement coverage, making the
final combine fail even when all tests pass. Coverage and assertions stay enabled.

Original review: https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/11#discussion_r3939585204
