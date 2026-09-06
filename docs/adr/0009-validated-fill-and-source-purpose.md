# ADR 0009: Validated fills and source-purpose boundaries

Status: proposed for human review. Corrects reviews 5557081140 and 5557168465.

| Requirement | Existing modules | Review gap | Regression | Dependency |
| --- | --- | --- | --- | --- |
| Blank correctness | CaseReviewer, ArithmeticCheck | Contradictory independent or terminal expected values passed | A1/A2 distinct blank cells, all constraint orders and tolerances | #15, then #16 |
| Blank propagation | FactorRuleEngine, DAG | Valid blank factor could not seed arithmetic | B and consecutive/intermediate/terminal blanks, zero/ratio, invalid trust | #15, then #16 |
| Source purpose | SourceRegistry, Controller, assemble | Real criteria examples could become case facts | Role/identity/version/mixed citations and actual extraction integration | Shared #15, assembly #16 |
| Approval eligibility | LocalApprovalStore | Unconfirmed or partly confirmed sides received receipts | Every side, historical isolated receipts, confirmed/native positives | #16 |
| Reviewer platform | LocalApprovalStore, document_cli | POSIX import broke generic CLI help | Platform simulation, help/operations/zero side effects, URI tests | #16 |

A ValidatedSlot separates the unchanged original observation, one concrete
candidate and the validated downstream value. Only an evidenced blank explicitly
marked derivable_blank may receive a proposed fill. Missing states, ambiguous
facts, nonblank raw text/excerpts, invalid sources and missing authority never
supply a trusted value. Grades remain strings and cannot feed percent arithmetic.

An independently verified factor/total is authoritative for a blank candidate.
Without an independent value, all resolved incoming constraints must propose the
same Decimal value. Different expectations remain needs_review even when their
tolerance ranges overlap: the current contract does not select a unique fill in
that case. Never select by order, average, largest tolerance or diagnostic text.
Every constraint is checked against that single candidate before coverage or
propagation. Independent expectations also apply. Terminal blanks use the same
validation as intermediate nodes. Zero is a valid candidate; ratio observations
normalize to percentage points. Present aggregates retain inclusive tolerance,
ROUND_HALF_UP and unchanged observed-value propagation. Per-factor rates/grades
stay exact. No original value, state, raw text, citation or score is rewritten.

SourcePurposes binds the actual selected forms and criteria documents from the
current registry. Default direct review requires exactly one of each; explicit
Controller selection is revalidated against full source identity/content. A role
label alone cannot authorize another forms identity/version.

| Use | Allowed evidence |
| --- | --- |
| Executable rules/applicability | Selected criteria |
| Target/comparable facts | Selected forms |
| Original values, fill slots, case contexts and empty columns | Selected forms |
| Arithmetic procedure | Selected forms/criteria or registered reference |
| Brief or other source numeric facts | Unsupported; requires review |

Each citation must also resolve its exact hash/version/page/region/bbox. Procedure
references never supply numeric leaves. There is no general source-role override.
Shared review validates purposes even for directly injected, authorized material;
#16 assembly also records invalid proposal uses without silently discarding them.

No public PDFWriter or FactorReviewResult schema changes. B's real writer,
immutable writer-time snapshot, multi-context PDF support, #9 jobs/deployment and
frontend remain future work. These tests create synthetic input PDFs and use
mocked providers/isolated test authority; live Bedrock, complete real-case goldens,
human approval and actual reviewed PDF output remain pending.

Assembly retains invalid proposals for inspection but adds explicit source_purpose
unresolved entries both for the originating page and for citation uses. A criteria
example cannot establish case context or cells merely by resolving. The shared
CaseReviewer repeats validation using current Controller-selected documents.
Approval eligibility remains separate from full-case completeness: unresolved
inventory/source-purpose findings still block review, even if all fact sides have
been confirmed. There is no approve/review circular dependency.

Receipt eligibility explicitly accepts measured/native_extraction native_numeric
sides with a named producer, or reviewer_confirmed sides with current reviewer and
side digest plus resolved evidence and no side ambiguity. Every side of every pair
must qualify. model_proposed, unknown provenance, absent/stale/other-reviewer
confirmation fail both approve and permits. Eligibility does not introduce another
confidence threshold; CaseReviewer applies the configured measured threshold.
Historical unconfirmed/partial receipts are rejected in place, never auto-rewritten.

The local reviewer workflow is supported on Linux and macOS with pwd, getuid,
ownership checks and owner-only 0700/0600 storage. Windows native approval is not
implemented; use a Linux environment. Generic imports and CLI help do not require
pwd. Reviewer operations check platform before reading material or creating keys,
receipts or confirmation. Unsupported systems return unsupported_reviewer_platform
and CLI exit 2, without a module traceback or identity/permission fallback.
The local URI helper accepts POSIX absolute file URIs and rejects Windows drive
and UNC forms; this does not implement Windows storage or ACL support. Tests
simulate missing pwd/getuid and an unsupported platform in subprocesses. Host
macOS and Linux CI positive checks are actual POSIX tests, not Windows validation.
