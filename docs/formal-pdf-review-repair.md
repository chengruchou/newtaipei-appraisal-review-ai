# Formal PDF output review repair

This repair enforces the existing local-output guarantees of
[ADR 0019](adr/0019-formal-multi-context-output-and-placeholders.md). It does not
change a service schema or the `PDFWriter.write_pdf` protocol.

## Protected downloaded artifact

`LocalPlaceholderBackfill.backfill` retains all caller-supplied
`PDFWriteRequest.protected_source_uris` and adds the digest-verified downloaded
artifact. The normal staged writer rejects destination paths, resolved parent
aliases, symlinks and hardlinks to protected sources. Its publication check
rechecks inode identity and destination aliases before the atomic rename, even
when `overwrite_existing=True`. A revealing write always needs a separate local
destination. The downloaded artifact is never used as a mutable template.

## Approved font snapshot

Preflight reads the font once into immutable bytes, checks the approved digest,
and measures glyphs from that snapshot. The internal `PDFPreflightPlan.font_bytes`
is also the sole font input for embedding. Rendering does not reopen the font
path. Registration uses a digest-bound name and checks ReportLab's registered
face bytes, failing closed if its PostScript-name cache would substitute another
font. The internal face-byte check is covered by an actual same-name cache
substitution regression; an unavailable byte record also fails closed.

`PDFRenderConfig.approved_font_sha256` stays optional for legacy callers.
Registry configuration always supplies the approved digest. The writer metadata
version remains `2`; this is an enforcement correction to that writer's existing
contract. Approval of a synthetic font does not establish approval of a formal
government font or template.

## Composition interface for the integration owner

Use the existing registry and writer APIs:

```python
entry = registry.select(template_id, template_version)
config = entry.render_configuration(
    base_config, font_name=approved_font_name, font_path=local_font_path
)
writer = LocalPDFWriter(render_config=config, template_policy=entry.policy)
request = PDFWriteRequest(
    source_uri=template_path.as_uri(),
    destination_uri=output_path.as_uri(),
    protected_source_uris=reviewed_source_uris,
    result=verified_comparisons[0],
    additional_results=verified_comparisons[1:],
    field_map=entry.field_map,
)
result = await writer.write_pdf(request)
```

Every comparison must have a unique exact context in one case; the map must
cover every comparison. `entry.contexts()` lists map contexts in field order.
The service manifest owner must bind every comparison and written field ID,
template/version, `entry.policy.template_sha256`,
`entry.policy.field_map_sha256`, `config.approved_font_sha256`, writer version
`2`, actual reopened output digest, and the run/attempt/source authorization.
This adapter repair does not implement that service-wide manifest migration.

The S3 wrapper advertises multiple-context support only if the underlying
attribute `is True`. Strings, integers, missing and auto-created mock attributes
cannot enable this path. Publication wrappers still reject revealing writers.
Use `LocalPlaceholderBackfill` only on the local privacy side with the original
request, local private mapping, downloaded artifact and expected artifact digest.

## Regression and local evidence

At original head `ca4149439ce65dbf8176043aadad13b5d76ba0f9`, the initial
regression suite produced 10 failures and 4 passes. Failures independently
covered destination aliases (including aliases introduced immediately before
publication), truthy capability coercion, and replacement of the approved font
after preflight. The corrected suite additionally covers a single font read for
measurement and embedding, and incompatible cached font bytes.

`tests/unit/test_template_registry.py` composes a serialized registry with the
real writer, writes two contexts plus an opaque token, reopens both pages and
checks the complete embedded field-ID manifest. It then produces a separate
local backfill and verifies that template and placeholder bytes are unchanged.
Its original synthetic box-glyph CJK fixture tests Unicode mapping and coverage;
it is not evidence of legible production CJK typography.

Reproduce with declared project PDF/dev dependencies, from this checkout:

```sh
mkdir -p artifacts/pr34/tmp
PYTHONPATH="$PWD/src" TMPDIR="$PWD/artifacts/pr34/tmp" python -m pytest \
  tests/unit/test_pdf_output_protection.py tests/unit/test_template_registry.py \
  --basetemp=artifacts/pr34/regressions
python -m ruff check .
python -m ruff format --check .
PYTHONPATH="$PWD/src" python -m mypy src
PYTHONPATH="$PWD/src" TMPDIR="$PWD/artifacts/pr34/tmp" python -m pytest \
  --basetemp=artifacts/pr34/full
```

The repair session uses the existing project-local runtime-deployment virtual
environment and stores logs and synthetic artifacts only under ignored
`artifacts/pr34/`. These are local adapter results, not AWS, complete service,
model-quality or formal business acceptance. No AWS calls are required.
