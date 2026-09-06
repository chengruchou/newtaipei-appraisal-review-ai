# ADR 0006: Source-grounded document candidates and local reviewer authorization

Status: proposed for human review. Implements #7 on the #8 schema-2 boundary.

LocalPDFParser reads only exact allowlisted paths, optionally pinned to a required
hash. It never discovers credentials or opens a caller-selected URI on import.
The opt-in CLI manifest requires hashes and identifies source role/version/date.
Native text, matrix cells, individual selection glyphs, page dimensions, rotation
and CropBox are retained. Parsing and rendering run in one lazily created process
worker because MuPDF does not support concurrent threads. Source bytes are never
modified. Extraction coordinates are unrotated CropBox bottom-left
points; rendered pages are deliberately unrotated, with explicit pixel conversion.
Text geometry and image localization do not prove semantic correctness.

Native table candidates use grade labels, column headers and source geometry.
Supported endpoint grammar produces candidate intervals/categories/matrices;
unsupported shapes, ambiguous units and missing semantics remain unresolved.
Native candidate detection counts are not accuracy or full inventory claims.
No source filename or fixed column alone identifies a case entity or rule.

BedrockDocumentExtractor accepts an injected Converse client and ExtractionConfig.
It sends one bounded rendered page and its parser registry to the selected model,
then validates a strict PageProposal. Source hashes/versions/pages/regions/boxes
and excerpts must resolve in the actual parser registry. Additional approval or
tool fields, malformed JSON, truncation, refusal and provider errors fail explicitly.
No model tool is executed. Model confidence is retained separately; reliability
is reset to model_proposed and observed confidence to zero. Confirmation preserves
that raw score and records a separate side-bound assertion (ADR 0008).
A document cannot instruct the service to alter rules or approve itself.

The validated target_sources and comparable_sources are canonical for each side.
The trusted extraction adapter derives legacy FactorObservation.evidence from the
actual source document/page/region: document_id, local URI, one-based page, region
ID, bounding box and coordinate system. The URI stays out of model input. Omitted
legacy evidence or optional location fields need no model guess. Explicit supplied
legacy fields must agree with a canonical reference on that same side; conflicting
metadata fails with conflicting_legacy_evidence. Empty or forged canonical
references fail with invalid_source_reference, even if legacy evidence exists.

Canonical evidence has confidence zero because localization is not calibrated
fact accuracy. Observation confidence remains zero and method stays model_proposed;
model confidence is separate. The adapter explicitly sets localization_only and
parser_registry provenance, names the canonical localization producer, and clears
any model-supplied confirmation or measured-provenance claim. The explicit
confirmation operation changes reviewed reliability, not raw scores or locations. Approval binds the
normalized, confirmed material digest; subsequent changes invalidate the receipt.
Existing prepared material is not silently rewritten or automatically approved.

Extraction is a separate capability from durable AWS jobs: ExtractionConfig needs
model/Region/inference bounds, not invented buckets or a jobs table. The opt-in
runner requires explicit profile, expected account/role and Region, verifies STS,
then checks selected-model modalities through the account API. Inference profiles
require explicit cross-region opt-in. No default profile, implicit resource creation
or fallback is used. SDK calls run off the event loop; bounded throttling retries
never apply to malformed output. Timeout does not start overlapping retries; SDK
read timeout also bounds the remaining worker call. #9 infrastructure is unchanged.

Page proposals preserve inventory separately from facts and observations. Every
parser-discovered criteria/forms table needs explicit accounting. Page continuations
merge inventory contexts, but duplicate facts/rules remain explicit conflicts for
review. Exact entity labels and scope must be reconciled across pages; no column
position is silently promoted to identity. Candidate assembly does not prove
semantic completeness. A complete reviewed inventory remains required by #8.

MaterialProvider supplies prepared candidate material through the existing parser,
fact extractor, rule provider and ReviewAdapters interfaces. Controller re-parses
the configured source documents before review. The same HTTP/invocation paths
perform calculations and return findings. No parallel review service or writer
protocol is introduced. Preparation can be repeated before human approval; review
can run without approval and reliably report needs_review with no artifact.

LocalApprovalStore is a minimal single-OS-reviewer trust boundary. Initialization
binds the actual UID/login. Private owner-only directory/key/receipt permissions,
an HMAC signature, timestamp and exact canonical material digest are checked on
read. That digest covers case/version, sources, applicability, complete inventory,
rules, normalized facts and original observations. Editing any of them invalidates
the receipt. Human confirmation and approval are distinct CLI operations; neither
is performed automatically on real documents. Model-declared approved is not a
credential. Missing approval or unresolved material still blocks completion.

The configured store and OS account are trusted deployment inputs. This workflow
does not defend against a compromised owning account or system administrator,
and is not an Internet identity/authorization service. Do not expose its CLI or
key to model tools or external request payloads. Future multi-user approval must
replace this adapter with authenticated reviewer identity and a managed key store.
The existing audit logger remains unchanged.

ReferenceCatalog provides versioned exact page/section citations and candidate
procedural checks. The supplied manual is a dated reference, not universal current
policy. Source-backed sum/equals checks still need case applicability and exact
material approval. Narrative background cannot become an approved rule. No vector
store, Knowledge Base or full RAG is introduced.

[Upstream concurrency constraint](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html).

Review 5122010245 inherits ADR 0007 source identity, source-cell binding, grounded
DAG and audit corrections from #15. ADR 0008 defines the confidence extension.
The actual confirm-facts CLI binds each side to current OS UID/login and preserves
all original scores. The local store refuses stale/missing confirmation and a
different reviewer before approval; permits also checks those bindings. Both
operations remain private trusted reviewer operations, not exposed model tools.
Old serialization receives unknown provenance for inspection only. Existing
receipts remain intact and invalid for changed material; no automatic re-signing.
Native rule candidates do not emit calibrated fact measurements and receive no
new numeric confidence claims in this correction.
