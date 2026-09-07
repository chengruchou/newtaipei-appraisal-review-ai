# ADR 0010: Separate case-data and PDF-template document identities

- Status: Proposed for human review
- Date: 2026-09-06
- Delivery: #5; coordinated application-contract correction

This ADR supersedes only ADR 0002's assumption that the controller can compare
writer output page count with the parsed case-data page count. The shared
`PDFWriteRequest`/`PDFWriteResult` protocol remains unchanged.

## Context

The review workflow extracts facts from a case-data or evaluation-basis document,
then may write verified values into a blank valuation-form template. These files
have different identities, page counts and trust roles. Reusing
`AgentReviewRequest.case_document_uri` as `PDFWriteRequest.source_uri` would copy
and edit the evidence document instead of the intended form template.

## Decision

`AgentReviewRequest` carries a separate optional `pdf_template_uri`.
`case_document_uri` remains an input to `DocumentParser` and `FactExtractor`.
When PDF output is requested, the controller requires a writer, template URI and
field map, and maps only `pdf_template_uri` to `PDFWriteRequest.source_uri`.
The PDF writer consumes the verified typed result and must not inspect the
case-data document to derive or reinterpret values.

The controller validates the returned destination and exact written field IDs.
It does not compare the output page count with the parsed case-data page count.
The concrete writer owns template page-count validation by reopening its output
and comparing it with the template source before returning `PDFWriteResult`.

## Consequences

- Data-source and template page counts may differ without causing a false writer
  failure.
- Source/destination alias checks apply to the template and completed output.
- A missing template for a requested output is a stable PDF write failure that
  preserves review findings and verification.
- Existing requests that do not ask for PDF output remain compatible because
  `pdf_template_uri` is optional.
- Synthetic demos identify a distinct template URI even though their fake writer
  deliberately creates no artifact.
- Production configuration still requires an approved template policy, field map
  and redistributable CJK font.
