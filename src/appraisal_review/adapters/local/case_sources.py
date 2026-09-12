"""Wire pinned, non-sensitive originals into a parsed source registry.

This is the direct path for a document set a reviewer has recorded as needing no
privacy processing. It reuses the existing native parser, so the resulting
`SourceDocument` pages, regions, coordinates and content hashes are produced the
same way as on any other path; only the sanitized-snapshot detour is absent.

The path is deliberately narrow:

- Only sources the manifest marks `direct_non_sensitive` travel it. A
  `privacy_required` source is refused here rather than quietly downgraded, and
  the scope comes from the reviewed source configuration, so no runtime
  confirmation step is introduced and nothing reports a privacy step as passed.
- Only pinned sources travel it. The pinned digest becomes the parser's expected
  hash, so a file that changed on disk fails instead of being parsed.
- Output templates never travel it. They are not sources of case facts.
- The resulting registry must satisfy the existing source-purpose selection, so a
  set that cannot name exactly one criteria and one forms document fails here.

Local paths are supplied by the operator at call time and are never recorded in
the manifest or in any report.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from appraisal_review.adapters.local.pdf_parser import DocumentInput, LocalPDFParser
from appraisal_review.domain.document_models import SourceRegistry
from appraisal_review.domain.source_manifest import CaseSourceManifest, SourceExpectation
from appraisal_review.domain.source_purpose import SourcePurposes
from appraisal_review.ports.workflow import ParsedDocument


class DirectSourceError(Exception):
    """The declared set cannot be wired. Carries no path or document content."""


def _eligible(
    manifest: CaseSourceManifest, bindings: Mapping[str, Path]
) -> list[SourceExpectation]:
    declared = {source.source_id for source in manifest.sources}
    unknown = set(bindings) - declared
    if unknown:
        raise DirectSourceError("binding_refers_to_undeclared_source")
    selected = []
    for expected in manifest.registry_sources():
        if expected.source_id not in bindings:
            raise DirectSourceError("registry_source_not_supplied")
        if expected.handling != "direct_non_sensitive":
            raise DirectSourceError("source_requires_the_privacy_route")
        if not expected.pinned:
            raise DirectSourceError("source_is_not_pinned")
        if expected.medium != "pdf":
            raise DirectSourceError("only_a_pdf_becomes_a_registry_document")
        selected.append(expected)
    if not selected:
        raise DirectSourceError("no_registry_source_declared")
    return selected


def document_inputs(
    manifest: CaseSourceManifest, bindings: Mapping[str, Path]
) -> list[DocumentInput]:
    """Build the parser allowlist from the manifest. Templates are excluded."""
    inputs = []
    for expected in _eligible(manifest, bindings):
        assert expected.registry_role is not None and expected.content_sha256 is not None
        path = Path(bindings[expected.source_id])
        if not path.is_file():
            raise DirectSourceError("supplied_original_is_not_a_file")
        inputs.append(
            DocumentInput(
                path=path,
                document_id=expected.source_id,
                version=expected.document_version(),
                role=expected.registry_role,
                expected_hash=expected.content_sha256,
            )
        )
    return inputs


async def parse_case_sources(
    manifest: CaseSourceManifest, bindings: Mapping[str, Path]
) -> tuple[SourceRegistry, list[ParsedDocument]]:
    """Parse every pinned direct source and return the registry it forms.

    A parse failure is reported as a bounded code. The parser already refuses a
    changed digest, an encrypted file and an oversized file.
    """
    inputs = document_inputs(manifest, bindings)
    parser = LocalPDFParser(inputs)
    parsed = []
    for entry in inputs:
        try:
            parsed.append(await parser.parse_document(entry.path.resolve().as_uri()))
        except DirectSourceError:
            raise
        except Exception:
            # Parser diagnostics can quote a path or document text.
            raise DirectSourceError("source_could_not_be_parsed") from None
    documents = [document.source for document in parsed]
    if any(document is None for document in documents):
        raise DirectSourceError("parsed_document_carries_no_source")
    registry = SourceRegistry(documents=[d for d in documents if d is not None])
    try:
        SourcePurposes.selected(registry)
    except ValueError:
        raise DirectSourceError("registry_cannot_select_one_document_per_role") from None
    return registry, parsed
