# Paired-source local privacy rehearsal

`scripts/privacy_browser_rehearsal.py` composes the production
`create_privacy_bridge` application, actual isolated PDF parsing/rasterization,
local AES-GCM mapping persistence/readback, the signed `CloudExportSink`, and
real C2 document admission/readback. The authored CJK criteria/forms sources and
post-admission extraction helper are in `scripts/privacy_raster_fixture.py`.
Candidate detection, output OCR observations and page-model responses are explicit
synthetic adapters. They do not establish real OCR/model accuracy or business
approval. Removing a known authored canary prevents the synthetic raster build.

## Construction

```python
fixture = create_privacy_rehearsal(
    private_directory,
    authority="127.0.0.1:8788",
    origin="http://127.0.0.1:4174",
    principal=server_principal,
    documents=document_service,
    signing_key=local_private_key,
    key_id=pinned_key_id,
    case_id=selected_case_id,
    on_admitted=record_committed_document,
    on_ready=register_candidate_case,
    results=authorized_current_publication_resolver,
)
```

The directory must be new and under this repository. Supply principal,
documents, signing_key and key_id together. The actual document verifier must
already trust the matching public key. A principal with multiple case grants
requires the explicit server-owned case_id. Omitting all four ports creates an
isolated local C2 test store; it does not create a backend review workbench.

The returned object exposes `app`, `config`, `session`, `token`, `source_ids`,
`documents`, `principal`, `sink`, `snapshot` and `close()`. Its repr omits secrets.
Call `close()` after the server stops; this locks local mapping keys. Keys remain
in memory, separate from encrypted mapping files. Rehearsal restart recovery is
not implemented.

Run only the independent bridge with:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python scripts/privacy_browser_rehearsal.py \
  --directory "$PWD/artifacts/privacy-browser-session" --port 8788
```

This writes a private `browser-private.json` in that directory, using mode 0600
and exclusive creation. It starts uvicorn on numeric loopback with forwarded
header trust and access logging disabled. No source review, export confirmation,
job, approval or result is created automatically.

## Exact paired handoff

Each source must independently pass the actual local page review and exact
sanitized preview/confirmation/export UI steps. A mapping/key/confirmation
failure results in zero C2 transfer calls. The first committed receipt for each
of `criteria` and `forms` becomes the one immutable pair for this rehearsal.
Only after both signed admissions and readbacks does the helper parse the new
sanitized bytes, inject the fixed synthetic page proposals and capture a new
candidate `RevisionSnapshot`. Image-only parser regions retain empty excerpts;
no original native citations, local original hashes or original private paths
become backend evidence. For existing local parser/assembler URI compatibility,
the helper creates a confined sanitized-input cache from exact C2 readback bytes;
its configured file URI belongs to the local candidate registry. HTTP callers
cannot choose those paths. Observations retain zero confidence and unconfirmed sides.

`on_admitted(receipt)` runs for every returned C2 receipt. `on_ready(snapshot)`
runs once for the paired candidate, in the bridge's export worker. A callback
failure is visible as a failed request; committed sink receipts remain available
for reconciliation and the callback is not automatically retried. Later exports
do not silently replace the registered pair.

The core workbench owns `await context.register_synthetic_case(full_fixture)`.
Its full fixture must use the exact sanitized forms bytes as its output template,
preserve placeholder positions, provide authored output coordinates/font and
bind the new sources. Capture the running workbench event loop before constructing
the bridge. A synchronous `on_ready` callback can schedule this coroutine with
`asyncio.run_coroutine_threadsafe` and await its bounded result in the export
worker. Registration returns an opaque review job ID before waiting for explicit
human side confirmations. It must not borrow authority from the original native
fixture. The main event loop remains available for actual human-task API/UI steps.

## Browser and restoration authority

`fixture.write_browser_fixture(path)` creates local private runner configuration
containing `bridge_url`, `origin`, `token`, `source_ids`, `sources` (each source ID
and safe authored `add_region`), and `app_path: "/privacy"`. The public frontend
configuration exposes only the bridge URL. Never serve the private manifest or
send its local token to the backend. Keep browser traces, screenshots and video
disabled when they could capture credentials or source data.

The parent launcher atomically preserves the initial private fields when adding
`review_job_id` after actual case admission. It adds `completed_job_id` and
`restore_result_id` only after current backend publication. The default rehearsal
resolver always rejects restoration. A real resolver must bind the opaque result
handle to an authorized current job/artifact, its exact downloaded bytes and the
forms mapping selected by the original document identity in the local manifest.
The bridge then uses the existing refill checks and writes a separate private
file. Transfer alone never grants result authority.

`web/e2e-real/privacy-flow.spec.ts` is owned with the frontend and exercises both
sources, explicit UI privacy confirmations, actual human-task confirmations,
backend publication and final local restoration. Its success must be reported
separately from Python HTTP tests. The standalone bridge command cannot establish
full backend/browser acceptance.

## Local check evidence

Five focused real HTTP rehearsal tests passed: paired C2 admission/new-source
assembly and run-source resolution, private runner configuration and unavailable
publication, mapping failure with zero admission, omitted authored canary, and
loopback Origin/Host/path rejection. The original CJK bytes remain unchanged;
new raster citations resolve with empty image excerpts and zero observation
confidence. Ruff, formatting and script mypy passed. These results do not claim
the separate combined backend/browser flow has completed.
