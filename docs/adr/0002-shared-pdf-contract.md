# ADR 0002: One typed PDF boundary and an explicit migration

- Status: Proposed for human review
- Date: 2026-09-05
- Delivery: #6; consumed by #4 and #5

The positional PDFWriter returned an unchecked URI. A and B must share one
request/result protocol before implementing parallel entry and rendering paths.

Public imports are `domain.pdf_models` and `ports.pdf.PDFWriter`. Field, result
and error value types are defined once in `domain.pdf_types` so `factor_models`
can include PDF metadata without a circular dependency on PDFWriteRequest's
FactorReviewResult. Public re-exports are aliases, not competing contracts.
Existing PDFField/PDFFieldMap imports from factor_models and PDFWriter imports
from ports.workflow remain valid; the positional writer call is removed with
its controller/test callers in this foundation commit. There is no automatic
string-to-result compatibility fallback.

PDFWriteRequest carries source/destination URI, result and field map. A write
requires explicit value references and one comparison context. Field IDs name
placements; they are never parsed as Python paths or evaluated as expressions.
Legacy field maps without references can still be loaded, but cannot be written.
The current result has one implicit target/comparable. References label that
context, but cannot prove case applicability or multi-comparable identity;
#8 owns that semantic expansion. B must reject unsupported contexts.

AgentReviewRun adds `pdf_result` and `pdf_error`. Noncritical PDF warnings remain
in pdf_result.warnings, separate from valuation warnings. Failures preserve
review/verification and return failed, no published URI. Only successful typed
results with the expected URI, source page count and exact written field set
permit completed. This is interface validation, not proof of PDF readability;
B must reopen and inspect the actual output before returning success.

URI validation is lexical and does no I/O. B resolves file paths/inodes to catch
symlink/hardlink aliases. file URIs require absolute local paths; S3 keys are
literal and must not be path-normalized. No queries/fragments or percent-escaped
S3 keys. The remote cloud API in #9 authorizes document IDs before resolving URIs.

The foundation owns shared dependencies, models, re-exports and minimal caller
migration. It adds httpx for real HTTP tests and boto3-stubs for strict typing
with or without the AWS runtime extra. B owns later optional PDF dependencies;
A owns entrypoint code. All three protected verifier/logger files stay unchanged.

The foundation PR targets main. A stacks on the foundation branch/SHA, and B may
branch from the same SHA. Shared schema changes after this point require an
explicit migration coordinated through #6, not separate redefinitions.
