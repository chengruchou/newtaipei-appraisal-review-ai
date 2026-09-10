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
template hash. It decrypts the session-owned mapping and requires one exact
manifest match, preserving the distinction between original local document ID
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

Seven focused tests passed using real signed C2 admission, encrypted local
mappings, actual PDF rendering/crop writes and an explicitly revocable
publication callback. They reject changed placeholder pixels, wrong mappings,
template/run/caller/content substitution and revoked authority. These adapter
tests do not establish real OCR accuracy or combined browser/job publication
acceptance; those require the separately launched current backend and real OCR.
