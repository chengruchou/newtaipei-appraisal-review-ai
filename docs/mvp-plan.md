# Delivery plan and ownership

The project delivers auditable appraisal review and assisted filling. Reading
PDFs and drawing output are stages; observed-value verification is essential.
The closed parent #3 remains history, not an active catch-all milestone.

| Order | Deliverable | Dependencies | Ownership / status |
|---|---|---|---|
| 1 | Shared PDF Contract Foundation #6, PR #10 | main b69ed74 | Shared models, ports, metadata, dev dependencies; code + local tests |
| 2A | Member A #4 | Exact #6 commit | Composition, sync HTTP, invocation adapter, synthetic runner; code + local tests |
| 2B | Member B #5 | Same #6 commit | PDF drawing, storage, fonts, field operations; planned, no implementation present |
| 3 | Chinese extraction #7 | #4 ports, #8 identity agreement | Parser/facts/candidate rules; planned, unassigned |
| 4 | Complete review #8 | #7 facts, #6 contracts | Observed values, applicability, sums, cross-form evidence/gate; planned, unassigned |
| 5 | AWS async integration #9 | #4/#5/#7/#8 | Jobs, dispatch/leases, Runtime, storage, observability; designed, unassigned |

Foundation SHA: e24265d7c4ef0bb5d2d74d0ed94bffa2052a08ed.
A stacks on codex/shared-pdf-contract. B can branch from this SHA immediately;
no waiting for A merge. Future shared changes are coordinated through #6.
A/B leave domain/verification.py, adapters/local/audit.py and
tests/unit/test_verification.py unchanged. Only #8 considers verifier expansion.
Human review remains required for all PRs; none are merged automatically.

## Acceptance stages

1. Foundation: schema/URI/placement/lookup and JSON tests, one writer protocol,
   warnings/errors preserved, compatibility imports. No rendering claims.
2. A: real HTTP and invocation parity, validation before invocation, explicit
   missing config, one call, failed/needs-review gates, typed PDF failure cases,
   no-credential imports and a runnable clearly synthetic demonstration.
3. B: original hash unchanged, multiple page dimensions/rotation/CropBox,
   glyph/overflow checks, blank-fill vs annotation vs genuine correction,
   readable output and atomic publication, injected mocked S3 transfer.
4. Extraction/full review: original-file checks with source hashes/pages,
   reviewer-approved candidate rules, observed/expected differences, precise
   identities, sums and copied values, coverage and conservative completion.
5. Cloud: independent execution/business states, authenticated document IDs,
   durable outbox/idempotency/lease recovery, bounded retries/DLQ, final manifest,
   live evidence and cleanup. An entry-only synthetic cloud smoke can precede
   document integration; it does not satisfy full cloud review acceptance.

The five initial factors (main road, average road width, drainage, terrain,
market proximity) remain a representative slice. Their success cannot approve
all other factors or empty comparison columns.

## Source checks performed on 2026-09-05

The official brief was read without modification. The supplied criteria have
9 pages; forms have 6 pages, including portrait/landscape A4 and landscape A3,
with no AcroForm widgets. Source thresholds and matrices confirm regional main
road 18m -> normal; individual front road 18m vs 6m -> +5%. The forms copy the
regional total into the comparison form; blank comparable columns remain blank.
A suspicious mixed-unit interval appears in the criteria and must remain
unresolved until confirmed. These are read-only source observations, not passing
extraction tests or permission to auto-approve rules.

#7/#8 acceptance includes those cases, endpoints, reverse matrix direction,
contradictory source text, empty comparable columns and mixed page dimensions.
Only synthetic fixtures enter Git. No source PDFs or OCR dumps are committed.

## Integration resources

The organizer supplies the intended AWS services; implementation planning is
not deferred on speculative service availability. Specific account access,
Region, model capabilities and actual quotas must still be verified. See
[architecture](architecture.md), [traceability](delivery-traceability.md),
[local runbook](member-a-runbook.md), and [cloud smoke plan](aws-smoke-plan.md).
