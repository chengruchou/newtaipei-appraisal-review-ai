# Member C continuation guide

Written 2026-09-12 for whoever takes over member C's line: source catalog, rules,
AWS adapters, and the deployment and conversion environment.

Read [member-c-readiness.md](member-c-readiness.md) first for the recorded state
and evidence. This file is the shorter operational answer to "what do I do next".

## Start here

```bash
cd <repo>
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Python 3.14 works. One environment fact that will waste your time otherwise: run
tests as `.venv/bin/python -m pytest`. The bare `pytest` entry point fails
collection on eight modules, because some tests import `tests.*` and `scripts.*` and
the repository root is not on `sys.path`. The CI workflow uses the bare form, which
is consistent with the empty hosted jobs in
[#49](https://github.com/chengruchou/newtaipei-appraisal-review-ai/issues/49). That
is worth fixing, but it belongs to whoever owns CI.

Confirm the baseline before changing anything:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src
.venv/bin/python -m pytest          # expect 3331 passed
```

## Where the organizer files are

Five originals sit in the ignored `artifacts/` directory. They are pinned by digest
in `configs/sources/newtaipei-shulin-1110901.json` and must never be committed.

```bash
.venv/bin/python scripts/check_case_sources.py \
  --manifest configs/sources/newtaipei-shulin-1110901.json \
  --file brief-case="artifacts/題目.pdf" \
  --file evaluation-basis="artifacts/評價基準明細表.pdf" \
  --file form-table-3="artifacts/表3地價區段勘查表.xlsx" \
  --file form-table-5="artifacts/表5影響地價區域因素分析明細表(住宅用地).xlsx" \
  --file form-table-4="artifacts/表4比較法調查估價表.xlsx"
```

Expect `sources_pinned` and exit 0. If you get `changed`, the file on disk is not
the file that was reviewed: stop and find out why rather than repinning.

The two PDFs parse into a registry through the direct non-privacy path:

```python
from appraisal_review.adapters.local.case_sources import parse_case_sources
from appraisal_review.domain.source_manifest import CaseSourceManifest

registry, parsed = await parse_case_sources(manifest, {"brief-case": ..., "evaluation-basis": ...})
```

The parser uses a spawn-based process pool, so drive it from a real script file,
not from `python -` or a heredoc.

## Next tasks, in the order I would do them

### 1. Install the headless office converter (needs operator approval)

This is the only blocker on the delivery flow's last step. `extra/libreoffice-still`
is available for this Arch host. Fonts are already fine: 79 families cover Han,
including Noto Sans and Noto Serif CJK TC.

```bash
.venv/bin/python -c 'from appraisal_review.adapters.local.workbook_pdf import LocalWorkbookConverter; print(LocalWorkbookConverter().capability())'
# today: state='converter_missing'
# after install: state='ready'
```

Then render one filled workbook and record the output digest and page count. Pass
`verify_text` with the visible worksheet heading, because that is what catches a
font fallback rendering headings as blank boxes.

Do not substitute a reportlab re-draw. It re-invents layout, merged anchors and
print range, so it is a second authoring step rather than a rendering of the
approved workbook. [ADR 0051](adr/0051-workbook-pdf-conversion.md) records why.

### 2. Resolve the model routing decision (needs organizer or operator input)

`us.anthropic.claude-sonnet-4-6` advertises destinations in `us-east-1`,
`us-east-2` and `us-west-2`. `us-east-2` is outside the permitted competition
regions, and the role cannot read Bedrock metadata there, so the complete
destination set cannot be verified either.

```bash
.venv/bin/python scripts/check_aws_readiness.py \
  --region us-west-2 \
  --model us.anthropic.claude-sonnet-4-6:system_profile \
  --permit-region us-east-1
```

Three options, in my order of preference: create one application inference profile
scoped to a single permitted region; obtain organizer approval for cross-region
routing; or select a model with on-demand support in the primary region. The first
creates a resource and needs approval. Details and trade-offs are in
[member-c-readiness.md](member-c-readiness.md).

Once one region is settled, `--model ...` produces a `destination_snapshot_sha256`
that goes straight into the private competition profile, and
`--profile <path>` then re-checks it on every later run.

### 3. Hand the rule bands to whoever owns calculation

The correction bands are printed in the evaluation basis and no longer need
guessing. Pages 1 to 5 are the regional factor basis, pages 6 to 9 the individual
factor basis. Extract them as reviewable candidates with citations; do not approve
them. Two traps:

- The two bases print different axis pairs. Regional is base section against target
  section; individual is comparable property against base parcel. Reversing the
  orientation inverts every correction, so it must be read off the printed table.
- The basis spells the base-parcel term with a variant character on pages 6 to 9.
  Text matching that assumes the standard spelling will silently miss those rows.

### 4. Everything still blocked on a person

| Blocker | Owner |
| --- | --- |
| The `P001` zoning description names both a mass-transit development area and a residential district | reviewer |
| Complete factor weights are not stated; equal weighting must not be assumed | reviewer |
| Whether brief page 6's printed percentages are the expected answer or an example to derive independently | organizer |
| Floor area ratio method; it must not be guessed from a ratio of the two figures | reviewer |
| Workbook file count, names, and whether hidden worksheets and external links stay | organizer |
| Whether any of this material may be transmitted to a cloud model | organizer |

These are recorded as `open_questions` in the manifest and reported by
`check_case_sources.py`, so they stay visible rather than living in someone's head.

## Decisions you should not quietly undo

- **The direct path is per source, not global.** `handling: direct_non_sensitive`
  plus a named `handling_reference` in the manifest is what lets a file skip privacy
  processing. A new case starts at `privacy_required`. Nothing reports a privacy
  step as passed, and the privacy modules are unchanged.
  [ADR 0050](adr/0050-direct-non-sensitive-source-path.md).
- **Local direct processing is not cloud data admission.** The cloud extraction path
  still requires an admitted sanitized snapshot. If you need this batch to reach a
  model, that is a shared-contract question for member A, because
  `SanitizedSourceReference` has no honest value for a batch with no privacy
  manifest. Do not invent a digest for that field.
- **An observed digest is not a pin.** `compare()` returns `unpinned`, distinct from
  `satisfied`. Nothing writes a pin automatically.
  [ADR 0049](adr/0049-declared-case-source-manifest.md).
- **Routing discovery refuses partial sets.** A subset understates where a request
  can travel, which is worse than no observation.
  [ADR 0048](adr/0048-observed-model-routing-evidence.md).
- **Required inference type follows the route.** `ON_DEMAND` for a direct foundation
  request, `INFERENCE_PROFILE` for a profile-routed one. Do not relax this to accept
  either, or a destination the profile cannot reach becomes acceptable.
- **Reports carry codes, not diagnostics.** SDK messages and converter output quote
  paths, endpoints and cell content, so failures surface a bounded code. Keep it
  that way.

## Two things to confirm with a person before publishing

1. `configs/sources/newtaipei-shulin-1110901.json` commits the sha256 digests of
   the five organizer files. Digests are not content, and the repository already
   pins digests of competition-provided documents in
   `domain/competition_profile.py`. Even so, confirm this is acceptable for
   case-derived documents, since `CONTRIBUTING.md` forbids committing the files
   themselves.
2. The manifest and one test contain Traditional Chinese worksheet names and
   headings from the official templates. These are structural metadata rather than
   case values, and the repository already documents worksheet names elsewhere, but
   confirm the scope.

No real account ID, role name, local path or organizer file name appears in any
tracked file. I checked before handing this over.
