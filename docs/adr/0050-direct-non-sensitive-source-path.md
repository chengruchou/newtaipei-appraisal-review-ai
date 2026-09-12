# ADR 0050: A per-source direct path for document sets that need no privacy processing

## Status

Accepted for implementation and local verification, scoped to the sources a
reviewer has named. It grants no cloud data admission, deletes no privacy
component, and does not apply to any future case by default.

## Context

A reviewer confirmed with a professional that one specific batch of documents, a
set of standard official forms, needs no privacy processing. The delivery flow for
that batch is upload, parse, check data and rules, calculate, fill the official
workbook, render that same workbook to PDF, preview and download. There is no
masking step, no privacy scan wait and no restoration step in it.

Three wrong ways to implement that were available. Hide the privacy steps in the
front end while the back end still requires them, which desynchronises the two and
leaves the flow broken. Report a privacy step as passed when it never ran, which
falsifies a trust record. Or delete the privacy modules, which would remove a
capability a future case with genuinely sensitive material still needs.

There was also a question of where the decision belongs. Asking the operator at
upload time would add exactly the confirmation step the change removes, and would
let a runtime click, rather than a reviewed decision, choose the trust path.

## Decision

Record the processing scope per declared source in the reviewed source
configuration. `SourceExpectation.handling` is either `privacy_required`, which is
the default, or `direct_non_sensitive`, and the latter is invalid without a
`handling_reference` naming the decision. The scope is therefore reviewable,
diffable and pinned alongside the file's digest, and it is never asked at runtime.

`adapters/local/case_sources.py` is the direct path. It reuses the existing native
parser, so pages, regions, coordinates, table cells, selection marks and content
hashes are produced exactly as on any other path; only the sanitized-snapshot
detour is absent. It refuses, with a bounded code and no path or content in the
message:

- a source whose handling is `privacy_required`, which is not downgraded silently;
- a source with no reviewer pin, since the pin becomes the parser's expected hash
  and a file changed on disk must fail rather than parse;
- an output template, which is not a source of case facts;
- a resulting registry that cannot select exactly one criteria and one forms
  document under the existing source-purpose rules.

Nothing on this path writes a privacy manifest, a sanitized reference or a privacy
outcome. A consumer that requires a sanitized snapshot still requires one; the
direct path does not satisfy it and does not pretend to. The privacy modules,
contracts and tests are unchanged.

Local file paths are supplied by the operator at call time and appear in no
manifest and no report.

## Consequences

The named batch parses from its originals with full provenance, and the pinned
digest ties every parsed document and citation to exact reviewed bytes. Whether a
file may skip privacy processing is now a reviewable line in configuration rather
than an implicit behaviour or a runtime prompt, and front end and back end agree
because both read the same recorded scope.

The scope does not generalise. A new case starts at `privacy_required`, and moving
a source to the direct path needs a new recorded decision by a person.

This does not admit any of this material to a cloud model. The cloud extraction
path still requires an admitted, sanitized snapshot, and the competition
data-admission policy is untouched: the case carries valuation amounts, which
remain a prohibited category pending an organizer clarification. Choosing the
direct path for local processing and admitting data for transmission are separate
decisions, and only the first has been made.
