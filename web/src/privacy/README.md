# Optional local privacy workbench

Mount `PrivacyWorkbench` from `PrivacyWorkbench.tsx` on a separate frontend route
with `bridgeBase` set by trusted deployment configuration to a loopback HTTP
origin. This component asks for its own server-issued development bridge token
and keeps it in component memory. It never reuses the cloud review token.
The browser supplies its real Origin; the bridge checks that Origin, loopback
Host and session on every PNG, PDF, read and mutation request.

The parent frontend router must mount the component after reviewing this
separate patch. These files do not modify the existing App, cloud client,
canonical service schema, or build configuration.

## Workflow and boundaries

1. List opaque authorized source handles and explicitly open one.
2. Fetch and display each original PNG through authenticated local fetch and a
   temporary blob URL. Only a loaded image can be marked reviewed.
3. Inspect highlighted regions, correct coordinates/category/entity grouping,
   add missed regions, or dismiss a detection with an explicit reason. Coordinates
   are bottom-left, unrotated CropBox-local PDF points. No confidence is edited.
4. Confirm the reviewed source and regions using the latest server-issued
   `X-Privacy-Review-Digest` and exact source/selection revision. No JavaScript
   reimplementation of the review digest is used.
5. Prepare and display the exact sanitized PDF and manifest. Verify the PDF
   SHA-256 against `manifest.sanitized_digest`, then require a loaded preview and
   explicit checkbox/button confirmation for that exact payload digest.
6. Transfer once through the local bridge's trusted C2 sink. An uncertain transfer
   locks further exports and retries until the operator reconciles the result.
7. Restore an authorized opaque result ID through the local bridge, verify
   returned PDF bytes against `FinalLocalManifest.final_digest`, and save the
   restored PDF using a temporary local blob URL.

The current document-v1 C2 upload accepts only the sanitized PDF and signed
manifest. `prepare()` sends `{}` and requires null reviewer text in the preview;
this UI does not accept text it would later discard. A separate human-response
flow is needed for optional locally sanitized reviewer text.

Source and review edits clear the visible export preview and its approval.
The backend independently rejects stale revisions and consumed previews. A UI
flag is not evidence of backend authorization, persistence or transfer success.
Original text, mapping material and local paths never enter cloud client calls.
Original pages and restored content are never assigned an unauthenticated URL.
Blob URLs are revoked when replaced or when the component unmounts.

## Contracts and verification

`contracts.json` exports the existing PR #37 Pydantic models
`PrivacyReviewView`, `AddPrivacyRegion`, `EditPrivacyRegion`,
`RemovePrivacyRegion`, `ReviewPrivacyPage`, `ConfirmPrivacyReview`,
`PrivacyManifest` and `FinalLocalManifest` in serialization mode. `$defs`
references are translated to OpenAPI component references solely to use the
existing TypeScript generator. This is a local consumer schema, not a cloud
API or a new domain contract. `schema.d.ts` is generated from it.

The runtime client validates these exact models and UUID4 formats. Small bridge
envelopes follow the implementation in `adapters/local/privacy_bridge.py`.
Keep the response digest header exposed in CORS. Model validators beyond JSON
Schema, admission, source identity and output authorization remain server-owned.

Focused tests are `tests/privacy-client.test.ts` and
`tests/privacy-ui.test.tsx`, using only synthetic data. They exercise page-load
gating, region edits, current digest use, explicit exact-preview confirmation,
one-use transfer recovery policy, opaque restoration, separate local transport,
and PDF hash checks. They are not real bridge/browser acceptance. Run the
configured integration fixture with an actual mounted route before claiming
that acceptance.
