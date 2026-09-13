# Integration status — 2026-09-13 08:00 (P0 round close)

Deployed: `appraisal-workbench:cand-6d759eb` on http://54.212.57.12 (whitelist-only).
Branch `feat/formal-report-approval`, HEAD `235ad3b`. Verdict: `real_e2e_complete = false`
（未達最低完整流程要求）— see `real-e2e-acceptance.json` for the ten-step evidence.

## What works on the deployed site, today

- **Accounts**: register → email verification → set password → login; code login;
  sessions 8h, revocable; approver mailboxes get PUBLISH + pinned-case membership
  at session issue (`REVIEW_APPROVER_EMAILS/_CASE_IDS`).
- **Case intake**: create case, upload materials (server-verified SHA-256), case list
  and detail pages; memberships re-granted at boot from durable intake records.
- **Candidates plane (named human, live-proven tonight)**: two real bus-stop
  candidates (山佳, 中洲街口 — real NTPC rows with coordinates and per-page evidence
  sha) registered and **confirmed with named-human receipts** on case 55642f92,
  revision f30bfabc, by actor 0153eb2c (chengruchou@gmail.com) under explicit chat
  authorization.
- **Map verification**: read-only Leaflet panel over candidate evidence geometry
  (straight-line distance labeled 非步行路徑; >5% divergence warning; no fabrication).
- **Sources**: SafeSourceReader live fetches; bus-stops full sweep 33,109 rows /
  668 樹林區 pinned to S3 with per-page sha256.
- **Bedrock chain (demo-principal jobs)**: real Converse via 1.2s-spaced dispatcher and
  trusted-assembly admission (mistral-large-3, us-west-2; us-east-2 fail-closed).
- **Exports machinery**: per-table exports, report approvals, and the new
  **pdf/excel/both ZIP bundles** (parent request; children replay; ZIP re-passes
  content-plane fences per file; manifest.json) — deployed with green gates
  (backend mypy 273 files, adoption+exports+approvals suites; web 480/480).
- **Adoption pipeline (S1)**: service, SQLite store, route deployed; CAS on revision,
  atomic batch, idempotent replay; refuses RUNNING/SUCCEEDED jobs.

## The one wall that kept the chain from closing (root-caused)

Email-login humans are minted as **uuid5** actors (stable per mailbox). The document
plane types identities as **pydantic UUID4** (`DocumentAuditEvent.actor_id`,
`ObjectLabels.uploader`, grant identities) and `opaque_uuid` enforced v4 — so
**POST /v1/review-jobs rejects every email-login human (422)**, which blocks the same
named human from owning a job, and with it adoption → tables → approval → downloads.
Existing demo jobs are SUCCEEDED — a state adoption *correctly* refuses.

Landed tonight: `235ad3b` relaxes `opaque_uuid` to server-authored v4/v5 (tested,
deliberately **not deployed** pre-deadline). Remaining scope (enumerated, ~small):
relax the three UUID4 model fields with their own review, then the full named-human
chain opens with **zero** further architecture work.

## Honest data ceiling

`artifacts/shulin-case/computed.json`: 44 computable corrections, 6 given values
verified, **87 missing inputs, 29 missing unique factors**; regional totals PARTIAL
and unusable as 區域因素調整百分率. Tables 3/4/5 therefore deliver partially with
explicit per-row blockers — never fabricated completeness.

## Next session (in order)

1. Relax the three UUID4 fields (+ tests), redeploy, then run the whole named-human
   chain live: job → adopt (receipts ae33cce2/34569e9c ready) → tables → approve →
   three ZIPs → revision-invalidation demo.
2. invalid_proposal tuning for the Bedrock proposal preflight.
3. Intake-case → review-revision bridge so the user's own uploaded PDF (case
   4800d788) drives the chain end to end.
