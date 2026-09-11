# KPI1 real-data scope and remaining gates

Inspection date: 2026-09-12, Asia/Taipei. This is a read-only assessment of
existing source documents, controlled preparation revision 003, repository
writer code, and available local assets. It does not authorize case conditions,
facts, rules, templates, or output. Runtime changes and acceptance belong to the
integration owner; this assessment did not execute a new runtime or PDF write.

The supported delivery claim is partial local preparation from actual documents,
with explicit human review still required. No admitted real template and field
map pair was found for this case. Implemented PDF writing machinery and existing
form examples do not establish that the real reporting step has passed.

## Source inventory and extraction strata

The aliases Forms, Criteria, and Manual identify the three supplied originals.
All page references below are one-based PDF page numbers, not printed page
labels. Private manifests retain original identity, version, byte count,
SHA-256, page inventory, and exact evidence coordinates. This document omits
private locations, case identifiers, parcel details, source excerpts, and dumps.

| Source | Complete page inventory | Native parsing | Existing actual OCR |
| --- | --- | --- | --- |
| Forms | Pages 1-6: 6 pages | All 6 pages represented; text on all 6 | Pages 1-6 |
| Criteria | Pages 1-9: 9 pages | All 9 pages represented; text on all 9 | Pages 1-9 |
| Manual | Pages 1-169: 169 pages | All 169 pages represented; text on 167; no native text on pages 2 and 4 | None in the inspected OCR inventory |
| Total | **184 pages** | **184 page records; 182 pages with native text** | **15 actual pages** |

Preparation 003 checked the original hashes and reparsed the original bytes with
the current `LocalPDFParser`, matching the complete parsed registry. Empty text
pages remain in the inventory. Their contents are not assumed blank or reviewed.

The separate OCR run used Tesseract 5.5.1 at 200 DPI with Traditional Chinese and
English assets. All 15 per-page output hashes still match its recorded run.
These are existing actual OCR results, including zero-confidence observations.
They were neither rerun nor replaced. Preparation 003 used current native
parsing and explicitly selected native/manual candidates; it did not execute
OCR or a model. OCR execution, native localization, semantic correctness, and
human approval are separate evidence strata.

Native parser defaults allow 200 pages and 100,000,000 bytes per document; all
three originals are below both limits. This does not certify every other path:
the isolated privacy PDF worker defaults to 100 pages, 20,000,000 bytes and
16,000,000 pixels per rendered page. Its manual-length sanitization, restoration,
and OCR paths were not exercised by this preparation. The 15-page OCR run is
not evidence of whole-manual OCR, timing, or pixel-limit acceptance.

Implementation references:
[native parser](../src/appraisal_review/adapters/local/pdf_parser.py),
[preparation](../src/appraisal_review/adapters/local/case_preparation.py), and
[isolated PDF limits](../src/appraisal_review/adapters/local/privacy/pdf_worker.py).

## Limited field and procedure inspection

Selected candidate anchors cover Forms pages 1-3, Criteria pages 2 and 7, and
Manual pages 1 and 52-54. The preparation reports no fully reviewed pages and
an incomplete semantic inventory. Pages without selected anchors remain listed;
native parsing alone does not establish their rule coverage.

The limited checks inspected the current-use selection, source context and
date labels, road-type and road-width candidates, observed regional subtotals,
their total, and the corresponding comparison-form carry-over. This is field
inspection for candidate preparation, not a signed human confirmation or an
independent accuracy benchmark. Older field-check artifacts are locating aids;
their scores, methods, or prewritten material are not current authority.

Criteria extraction detected 47 native candidates, of which 37 fit supported
native candidate formats. Three were selected: regional main-road width,
individual road type, and individual front-road width. The other candidates
were not thereby accepted. A comparable regional road width is missing from
the controlled selection; the target width cannot supply that missing fact.
The two selected individual road adjustments do not validate the whole
individual-factor total, valuation, or every comparison column.

Manual page 53, printed page 49, supplies actual procedure evidence for adding
regional adjustment subtotals and carrying the result into the comparison or
income form. The sum anchors are `p53-b6` through `p53-b9`; the carry anchors
are `p53-b10` and `p53-b11`. Forms page 2 supplies the selected subtotal and
total observations; page 3 supplies the destination observation. The two
deterministic diagnostics agree for those observed candidates. They remain
`needs_review`, with `diagnostic_only=true` and approval pending. They do not
establish that the underlying factors or the procedure's applicability are
correct. Context and exceptions from Manual pages 52-54 remain relevant.

Four individual fact sides in revision 003 have exact, verified value/unit or
text anchors and no concrete side-level ambiguity. Their derived reliability
is `localization_only / parser_registry`; methods remain `native_proposed` or
`manual_proposed`. Raw observations and `EvidenceRef` confidence remain zero,
`model_confidence` remains null, and no confirmation is present. This allows
an explicit review to address those facts without claiming semantic confidence.
Missing anchors, unknown provenance, ambiguous selections, missing required
units, and actual unresolved readings remain blocked. Fact confirmation does
not approve conditions, rules, or exact material. See
[confidence provenance](adr/0008-measured-confidence-and-human-confirmation.md).

## Source-backed conditions that remain unresolved

These are English paraphrases of candidate meanings. Exact original text,
coordinates, and source hashes remain in the private condition-evidence record.
The configured resolver identity is a query, not a confirmed case identity.

| Condition | Source anchor | Supported observation and remaining decision |
| --- | --- | --- |
| District | Forms page 1 `p1-b1`; page 3 `p3-t0-r2-c3` | The target document supports a Jinshan, New Taipei district candidate. Confirm the target/comparable binding and scope; an address elsewhere in a document cannot substitute for this evidence. |
| Physical current use | Forms page 1 `p1-b21`, `p1-mark3`, `p1-mark0` | Mixed residential/commercial use is marked; the commercial-use choice is unmarked. The current-use selection must not silently become commercial-only. |
| Regulatory zoning | Forms page 3 `p3-b42`; Forms page 1 zoning row | Second-category commercial zoning is a distinct zoning observation. It does not prove physical current use or resolve which criteria apply. |
| Analysis category | Forms page 2 `p2-b0`; Criteria page 7 `p7-b1` | The supplied analysis and individual criteria use a commercial category. Its applicability to the mixed-use subject still requires review. |
| Valuation time base | Forms page 3 `p3-b24`, with the printed date context | A 2025 valuation base date is proposed from the actual page using ROC-year conversion. Survey, transaction, publication, and rule-effective dates have different meanings. |
| Section and comparison context | Forms page 2 `p2-b22`; page 3 `p3-t0-r6-c3` and target/comparable columns; page 4 acquisition-section heading | Benchmark/comparison and acquisition sections differ. Choosing the benchmark context does not confirm one section for the whole case. |
| Manual publication and applicability | Manual page 1 `p1-b16`; procedure context on pages 52-54 | The publication is March 2015. No inspected evidence establishes applicability to the 2025 valuation date; no effective interval has been confirmed. |
| Criteria period and geographic scope | Criteria page 7 heading and selected tables; Manual page 53 scope guidance | Local criteria must be checked against district, section, category, date, and exceptions. Both catalog entries remain candidates with unknown effective periods. |

Required decisions include applicability evidence, resolution of use/category
and section/context ambiguities, and confirmation of the exact selected source
versions and scopes. A publication date is not an effective-date approval.
Confirming one fact does not settle these decisions.

## Actual template and map availability

The repository provides a versioned template registry and deterministic writer.
The inspected code, examples, fixture assets, and real preparation contain no
admitted template version, complete real field map, and approved font set bound
to these supplied originals. This finding is limited to inspected local assets;
it is not a claim that no official template exists elsewhere.

| Existing asset | What is actually available | Reporting status |
| --- | --- | --- |
| Supplied Forms, 6 pages | Populated case documents with real table geometry; zero canonical AcroForm fields and zero page widgets | Evidence documents, not an admitted blank template. No reviewed writer field map or editable/reference page policy found. |
| Manual page 79, printed page 75 | An index of form formats and examples | A source locator; not a registry or machine-readable field map. |
| Manual page 101, printed page 97, Attachment 8 / Table 5-2 | Commercial regional-factor form example with three comparison columns and existing example entries | A reference layout. It is not a blank, approved template for this case. |
| Manual page 105, printed page 101, Attachment 12 / Table 4 | Comparison-form example with existing example entries and PDF rotation 270 degrees | A reference layout. Its geometry and rotation differ from supplied Forms page 3. No coordinate map can be reused by title alone. |
| Repository synthetic fixtures | Explicit synthetic templates/maps and synthetic CJK or test font bindings | Regression coverage for writer mechanics only; never real-template acceptance. |

The entire Manual also has zero canonical AcroForm fields and zero page
widgets. Embedded table examples are not interactive fields. Existing extraction
regions and field-check annotations locate source observations; they do not
declare authorized write regions or constitute a `PDFFieldMap`.

The supplied Forms have heterogeneous unrotated CropBox dimensions, in PDF
points: pages 1-2 are 595.2 by 841.68; page 3 is 841.68 by 595.2; pages 4-6 are
1190.4 by 841.68. All six have rotation zero. A single assumed page size, a
rendered-image rectangle, or coordinates from the manual would be insufficient.

`LocalPDFWriter` supports `fill_blank`, `annotate`, and a restricted `correct`
operation. Filling requires genuinely unoccupied regions; populated cells do
not qualify. Correction supports a restricted printable ASCII/Base-14 Type 1
text subset. The supplied Forms include embedded Type0 and TrueType resources;
their correction compatibility has not been proven. Annotation preserves
underlying content, but its approved placement and visual readability still
require review. No operation was attempted for this assessment.

An admitted configuration must bind exact template bytes and the canonical
complete field-map digest, classify every page, declare approved font bytes,
and use one-based coordinates in unrotated CropBox space. Each value reference
must resolve to the exact verified comparison and supported value. The writer
then checks geometry, text fit, fonts, source protection, and reopened output
before atomic publication. These checks do not create an approval.

References: [template registry](../src/appraisal_review/adapters/local/template_registry.py),
[writer](../src/appraisal_review/adapters/local/pdf_writer.py),
[preflight](../src/appraisal_review/adapters/local/pdf_preflight.py),
[writer migration](pdf-writer-migration.md),
[template identity separation](adr/0010-separate-pdf-template-source.md), and
[template policy binding](adr/0012-bind-pdf-inputs-and-template-policy.md).

## Minimal integration option for consideration

The integration owner can reuse `LocalWriterConfiguration` under
`LocalServiceConfiguration.writer`, after obtaining a genuinely reviewed
template/map/font configuration. That existing configuration accepts the
template, output directory, field map, policy, and render settings. It does not
discover or approve those assets. See
[local service composition](../src/appraisal_review/adapters/local/service.py).

The inspected original-workbench executor explicitly uses `pdf_writer=None`
and `authorization=None`; its prepared service also has no writer configuration.
Therefore configuring an asset alone would not enable its reporting path.
Main must explicitly compose the existing writer, preserve canonical execution
and approval gates, and bind `pdf_template_uri` and the matching map on the
verified request. This is an integration proposal, not an implemented change.

Keep the template identity distinct from case evidence and protect all selected
source identities from destination aliasing. Require the real condition, rule,
inventory, exact-material, and output gates before a write. Do not derive
approved metadata from the candidate PDF's own digest, reuse a synthetic map,
or convert diagnostic observations into verified writer results.

The adapter supports multiple contexts, while the reference local service's
confined wrapper does not declare that capability. Main must use a composed
path that truthfully supports every verified context or retain the unsupported
state. A map cannot silently drop other verified comparisons. Generic manual
procedure diagnostics also require an explicit supported result mapping; their
sidecar values are not automatically writer inputs.

Output acceptance still requires a new protected artifact, reopening and visual
checks of the actual mapped fields, and provenance tying the report to the case
revision, bundle, source versions, calculator, and template/map version. The
writer's version/field-ID metadata alone does not supply the full bundle trace.

## Local evidence versus remaining work

Actual local evidence covers original hash/page inventories, native registry
reproduction, the separate 15-page OCR run, candidate conditions, and a bundle
with two distinct sources and purposes: Manual as `general_rules / procedure`
and Criteria as `district_basis / factor_rules`. Source identities remain
separate through `RuleCatalog`, `resolve_rule_sources`, `RuleBundle`, and the
current document assembler. The total/carry diagnostics exercise real procedure
evidence alongside actual local criteria and form observations.

That establishes multi-source candidate preparation, not a completed
multi-source case calculation or designated-template report. Rule and catalog
approval, effective-period evidence, condition resolution, complete scoped
inventory, real confirmation/authority, template/map/font admission, writer
composition, and actual report acceptance remain distinct gates. Main's
separately reported API/browser checks are outside this read-only assessment.

Whole-manual semantic coverage, arbitrary template support, additional
independent real cases, cross-district accuracy, external-model extraction,
cloud adapters, and venue integration are future work. Synthetic unit fixtures
remain regression evidence only. This assessment created no approval, receipt,
new case preparation, template, or PDF output, and changed no source code.
