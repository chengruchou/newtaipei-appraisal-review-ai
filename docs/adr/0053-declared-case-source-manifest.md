# ADR 0053: Declared case sources are an expectation, never evidence

## Status

Accepted for implementation and local verification. It pins no organizer file,
approves no rule, no weight, no grade and no value, and grants no cloud data
admission.

## Context

The review pipeline consumes a `SourceRegistry` whose documents already carry
exact page geometry, regions, digests and roles. That registry can only be built
by parsing the real organizer files. When those files are not available locally,
the previous options were both wrong: invent a placeholder registry, or record
nothing and lose the decisions that do not depend on the bytes.

Several of those decisions are not derivable from the files at all and must be
written down and reviewed regardless. Which document supplies rules and which
supplies case values. Which labelled property is the comparison base: in the
studied case the brief presents the comparables first and the base last, so page
order is actively misleading. Which effective date applies, and how the Republic
of China notation maps to it. Which questions block rule selection, calculation or
output, and who must answer them.

An observed digest is also not the same thing as an approved version. Anything
that writes an observed digest back into a declaration silently converts a
measurement into a pin.

## Decision

Add `domain/source_manifest.py`. A `CaseSourceManifest` declares, for one case,
the expected organizer files with their medium, intended usage, registry role,
purposes, optional pinned digest and optional declared structure; the case's
district, effective date and labelled subject roles; and the open questions with
their owner and what each blocks.

The manifest cannot become evidence.

- `content_sha256` is optional and defaults to absent. `unpinned` is a distinct
  reported state from `satisfied`. `compare` returns `unpinned` for a supplied
  file that has no reviewer pin, and never writes a pin anywhere.
- Declared page and worksheet counts are expectations to check a supplied file
  against, not measurements of one. A mismatch is reported as
  `structure_differs`; a digest mismatch is reported as `changed`.
- Exactly one subject and at least one comparable are required, labels are
  unique, and `brief_page` is recorded but is never how a role is decided.
- Exactly one `criteria` and one `forms` source are required, because
  `SourcePurposes.selected` requires exactly one current document per selected
  role. Two candidates for either role is an explicit selection decision, not a
  default.
- Rule evidence requires the `criteria` role and case evidence requires the
  `forms` role, matching `SourcePurposes.allows`.
- An output template is not a registry source. Workbooks consumed by the template
  registry declare no registry role, so a template can never be mistaken for a
  source of case facts.
- The domain module stays provider-neutral. PDF and worksheet inspection lives in
  `scripts/check_case_sources.py`.

`scripts/check_case_sources.py` reports per declared source whether a real file
was supplied and how it compares. It opens originals read-only. For a workbook it
reads only `xl/workbook.xml`, so no cell value, formula, external link or hidden
example row is loaded. For a PDF it reads the page count without rendering or
extracting text. The report contains digests, sizes and structure, never a path,
a file name or document content, and it keeps `rule_approval` and
`cloud_admission` at `not_granted`.

`configs/sources/newtaipei-shulin-1110901.json` is the first instance. Every
`content_sha256` is absent because no organizer file has been supplied to this
working tree.

## Consequences

The source list, role assignment, effective date and blocking questions are
reviewable and testable now, and the checker turns "we do not have the files" into
a specific list of which files are missing rather than a general absence. When a
reviewer supplies the originals, the same checker reports the observed digests for
that reviewer to pin.

This does not build a `SourceRegistry`, propose a rule, or select an applicable
rule set. The declared structure counts come from a human's reading of the
originals and are themselves subject to correction when a real file is checked. A
manifest whose sources are all unpinned cannot support any formal output, and the
recorded open questions stay open until the organizer or reviewer answers them.
