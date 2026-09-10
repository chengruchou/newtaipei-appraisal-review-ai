# Exact authored raster publication assets

Raster sanitization deliberately removes the source PDF text layer. Consequently,
the native `SYNTHETIC SOURCE` and `SYNTHETIC OUTPUT` text-marker checks cannot
recognize a valid image-only sanitized template or its derived output. Adding
markers after export would change the exact approved bytes and is not supported.

`PublicationInputs.raster_assets` is an optional, server-only
`TrustedRasterPublication` capability. Its default is `None`, retaining all
native marker checks. The existing `fixed_synthetic_assets=True` flag alone
does not enable the raster path.

```python
TrustedRasterPublication(
    source_versions=exact_authored_source_versions,
    template_hash=exact_sanitized_forms_hash,
    authorization=fixture.authorize(current_snapshot),
)
```

The independent authorization implements the existing `ReviewAuthorization`
port. It must bind current confirmed material and the fixture's originally
configured source, rule, template and font assets. No request body, model value
or newly observed hash can create that authority. The launcher opts in only for
its explicitly registered authored raster fixture and retains the exact source
version pins across restart. New source versions require a newly authorized
registration.

The projection requires an exact source-version set with no duplicates, an
image-only current source registry, and exactly one selected forms document.
The pinned template hash must equal the selected forms hash, configured template
policy hash and the hash of the actual template bytes. Independent authorization
must permit the exact current material. These checks run before marker bypass
and again after PDF verification.

Only the three native source/template/output marker checks are conditional.
Source file identity and byte hashes, current registry, calculation contexts,
writer evidence and result digest, output confinement, field-map/font hashes,
placeholder/original-content checks, reopened output metadata, page/field counts,
preflight, effect verification and the separate final publication authorization
remain in force. Output bytes are not modified by this change.

The regression first reproduced a real Controller-completed raster case failing
at the native source-marker check. It uses actual HTTP privacy approval/export,
encrypted mapping and C2 admission, then explicit observation confirmations and
the actual PDF writer. The authorized raster output must pass; omitted authority,
boolean flags, incomplete source sets, wrong template hashes and denied authority
must fail. Existing native publication tests exercise unchanged defaults.
