# Current publication to local restoration

`adapters/local/privacy_restore_resolver.py` provides `LocalRestoreCoordinator`,
an implementation of the existing `PrivacyBridgeResultResolver` port. It returns
the plan, publisher, authority, processor, OCR and private verified download used
by the bridge's real `LocalPrivacyRefillExecutor`. It does not grant publication
or business approval and never transfers the restored PDF.

```python
coordinator = LocalRestoreCoordinator(
    workspace=private_workspace,
    session=privacy_session,
    documents=actual_c2_service,
    publication=current_authorized_publication,
    processor=IsolatedPrivacyRefillProcessor(private_workspace),
    ocr=actual_local_output_ocr,
)
privacy_session.results = coordinator
```

The synchronous callback accepts `(principal_id: str, result_id: UUID)` and
returns the server-only frozen `RestorePublication(principal, job_id, run,
artifact, pdf, forms)`. The values use existing `Principal`, `RunReference`,
`PublishedArtifact`, exact PDF bytes and `DocumentMetadata` types. They must
never be reconstructed from HTTP input. The launcher owns the opaque result-ID
catalog. Every callback must read the current authorized job/result and retrieve
bytes through `CommittedResultResolver.verified_bytes`. A historical object,
matching hash, completed flag or previously valid download is insufficient.
Use the committed result body's run/attempt, not a terminal job projection which
may have cleared its claimed attempt. Do not block on the same event loop from
this synchronous callback.

The coordinator independently checks actor/case, run/attempt/object identity,
actual content hash/size, exact forms C2 metadata/readback, source versions and
template hash. Only a successful bridge transfer records an internal binding
from the complete canonical manifest to its session-owned mapping ID. Failed
transfer handles remain available for explicit reconciliation, but are not
candidates for unrelated artifact restoration. The coordinator selects this
exact binding before decrypting; it never scans the case's other maps or skips
a read failure. The selected record must still decrypt, remain accessible and
unexpired, and match the map, case, source document and complete manifest.
This preserves the distinction between original local document ID
and new C2 document ID. Existing map/entity/occurrence IDs remain unchanged;
each existing occurrence ID also identifies its local restoration field.

Before making a plan or download, the actual isolated processor renders both
the exact sanitized forms template and published PDF. Every placeholder region
must have identical pixels at the same coordinates. The plan restores approved
original crops into those existing regions. This retains the actual CJK crop
without inventing text or requiring a newly chosen font. The existing worker
checks inserted crop image bytes and unchanged pixels outside those regions.

The download uses a newly created private directory and an exclusive file. The
bridge independently confines and hashes that file, then writes restoration to
another fresh private directory. Originals, sanitized templates and downloads
remain separate. Cached plans still recheck current publication and mapping
authority. Currentness is checked again before and after slow refill processing.

OCR is an explicit required port. The coordinator supplies no simulated OCR,
confidence override or fallback. Existing `TesseractPrivacyOutputOCR` requires
both pinned English and Traditional Chinese assets. Missing assets, low scores,
unreadable or residual tokens continue to fail the existing executor gate.

Each local HTTP response exposes a server-generated `X-Privacy-Request-Id`.
The existing generic rejection body and HTTP status remain unchanged. A trusted
local composition can supply `PrivacyBridgeConfig.diagnostic` to receive a
bounded refill failure record: request ID, status, error code, stage, one-based
page, fixed reason, observation count and low-confidence count. It receives no
raw text, coordinates, paths, source identifiers or PDFs. Diagnostic sink failure
cannot change rejection into success. The combined rehearsal saves these
records privately and includes the same request ID in its original OCR evidence.

Focused tests exercise real signed C2 admission, encrypted local
mappings, actual PDF rendering/crop writes and an explicitly revocable
publication callback. They reject changed placeholder pixels, wrong mappings,
template/run/caller/content substitution and revoked authority. Additional
regressions retain a failed export's corrupted ciphertext without reading it
while restoring a different successful export, and reject damaged selected
ciphertext, missing bindings and substituted map records. These adapter
tests do not establish real OCR accuracy or combined browser/job publication
acceptance; those require the separately launched current backend and real OCR.
