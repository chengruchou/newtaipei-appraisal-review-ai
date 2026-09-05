# ADR 0004: Preserve legacy validation errors and publish the review envelope

Status: accepted for implementation; PR review remains required.

## Context

Review discussion r3939585201 on PR #11 found that a global validation handler
changed the existing /v1/validate contract and disagreed with OpenAPI. Success
responses and deterministic review semantics do not need to change.

## Decision

Only the routed /v1/reviews endpoint uses EntryProblemResponse, containing one
EntryProblem under error. HTTP 422, 503 and 500 explicitly declare this model.
EntryProblem.response() serializes the same model for HTTP and invocation.
Routing identity, rather than a URL prefix, also works under mounted/root paths.

Legacy validation retains HTTPValidationError: detail contains loc, type and
msg. Raw input and validator context are omitted; custom value/assertion error
messages become a generic message because they can interpolate document text.
No success response, workflow rule or completion gate changes.

## Evidence

test_api_error_contract.py validates actual JSON against each OpenAPI response
schema, rejects wrong envelopes, checks missing/malformed legacy input, covers
all review failure statuses and verifies invocation parity and call counts.
scripts/http_smoke.py checks both error shapes on a real loopback server.
jsonschema is a development-only dependency; no runtime dependency is added.

Original review: https://github.com/chengruchou/newtaipei-appraisal-review-ai/pull/11#discussion_r3939585201
