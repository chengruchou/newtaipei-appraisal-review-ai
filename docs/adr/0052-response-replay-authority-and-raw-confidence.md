# Response replay authority and original confidence

Status: accepted for the existing human-task application service.
Date: 2026-09-12.

## Context

An exact retry must retrieve the committed response before stale task/revision
admission: the first successful response already consumed that version. However,
the service previously checked only job ownership and REVIEW before returning a
receipt. A principal that retained REVIEW but lost CONFIRM or CORRECT could replay
the response. Changing the same actor identifier's kind to system or model also
bypassed the human-actor check on this fast path.

Corrections additionally assigned the minimum of stored confidence and submitted
confidence to the revised observation. This prevented confidence inflation but
still allowed the command to rewrite a measured extraction score. Human adoption
and extraction confidence represent different assertions.

## Decision

After authorizing the task and job, check the task's current required permission
and human actor kind before reading a response receipt. Only then compare the
stored payload digest. Apply task state/version, material/side digest, revision
and allowed-response admission to first submissions as before. Preserve the
authoritative replay check inside the store transaction.

An authorized retry returns its original receipt. A revoked write permission or
nonhuman actor returns the existing unauthorized error. Missing case access or
foreign ownership retains the existing not-found behavior. Restoring authority
allows recovery of the original receipt without another revision or resumed run.

Keep the observation's measured confidence exactly unchanged during correction,
including zero. The submitted confidence is retained in the proposal ledger only.
The applied change records the stored score and citations. Parent snapshots remain
unchanged and corrections still invalidate confirmations and native trust through
the existing revision operation. A measured score is not authority for the new value.

## Compatibility and limits

No shared schema, port, route or generated client changes are required. Successful
receipt shapes and idempotency semantics are unchanged. Clients that relied on
replay after losing write permission now receive 403. Proposals with lower scores
no longer lower the raw score in new revisions; historical revisions are not rewritten.

Authorization uses the Principal supplied by the trusted resolver for this request.
This does not implement a persistent grant store or an atomic policy-version check
against revocation during an in-flight transaction. A future durable composition
must define that boundary explicitly.

This change does not implement the planned manual/adopted-value schema. The current
revision model still labels corrected values model_proposed; changing that requires
A's shared-contract migration. It also does not make the reference stores durable
or authorize real-case rules, facts or publication.

## Verification

Service regressions cover CONFIRM and CORRECT replay after removal of the write
permission, REVIEW, all permissions, case access, owner identity or human actor
kind. They assert unchanged task/job/revision state and exact replay after authority
is restored. HTTP regressions assert 403 followed by the unchanged receipt when
permission is restored. Confidence regressions cover zero, low and high stored
scores against absent, lower and higher proposal scores, while checking original
evidence, change ledger, parent snapshots and cleared confirmation.

## Integration clarification: evidence supply

When integrating the later SUPPLY_EVIDENCE action, preserve the same raw-confidence
rule. Its pinned-registry citation validation and evidence replacement remain
active; generated EvidenceRef records retain observation confidence rather than a
proposal score. Both the observation and its newly attached evidence retain 0.5
when a responder proposes 0.0 against raw 0.5. Original zero remains zero, and the
proposal's value remains auditable in the change ledger. Evidence supply does not
replace the next confirmation or exact-material approval. See the
[PR 56 repair record](../pr-56-review-repair.md) for the combined regression matrix.
