#!/usr/bin/env python3
"""Check supplied case source files against a declared manifest. Nothing is modified.

The manifest states which organizer files a case needs, what each is used for and
which questions are still open. This checker reports, for each declared source,
whether a real file was supplied, its observed digest and structure, and whether it
matches the expectation.

It never writes a pin into the manifest. An observed digest is a measurement; only
a reviewer decides it is the approved version. Originals are opened read-only, and
for a workbook only the package's worksheet declaration is read, so no cell value,
formula, external link or hidden example row is loaded.

Usage:

    python scripts/check_case_sources.py \
      --manifest configs/sources/newtaipei-shulin-1110901.json \
      --file brief-case=/private/path/brief.pdf \
      --file evaluation-basis=/private/path/basis.pdf

Paths are private. The report contains digests, sizes and structure, never a path,
a file name or any document content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from appraisal_review.domain.source_manifest import CaseSourceManifest, SourceExpectation, compare

MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Observation:
    source_id: str
    usage: str
    handling: str
    state: str
    detail: str
    content_sha256: str | None = None
    byte_size: int | None = None
    structure: dict[str, int | str] | None = None
    differences: tuple[str, ...] = ()


def workbook_structure(content: bytes) -> dict[str, int | str]:
    """Worksheet counts and the single visible worksheet from `xl/workbook.xml` only."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as package:
            raw = package.read("xl/workbook.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as error:
        raise ValueError("Unreadable workbook package") from error
    root = ElementTree.fromstring(raw)
    sheets = [element for element in root.iter() if re.sub(r"^\{.*\}", "", element.tag) == "sheet"]
    if not sheets:
        raise ValueError("Workbook declares no worksheet")
    visible = [sheet for sheet in sheets if sheet.get("state", "visible") == "visible"]
    structure: dict[str, int | str] = {
        "sheet_count": len(sheets),
        "hidden_sheet_count": len(sheets) - len(visible),
    }
    if len(visible) == 1:
        structure["visible_worksheet"] = visible[0].get("name", "")
    return structure


def pdf_structure(content: bytes) -> dict[str, int | str]:
    """Page count and text coverage. No page is rendered and no text is retained."""
    try:
        import pymupdf
    except ImportError as error:
        raise ValueError("PDF inspection requires the documents extra") from error
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
            if document.is_encrypted:
                raise ValueError("Encrypted PDF")
            # Count pages that would need a raster route; the text itself is discarded.
            without_text = sum(1 for page in document if not page.get_text().strip())
            return {
                "page_count": int(document.page_count),
                "pages_without_text": without_text,
            }
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("Unreadable PDF") from error


def observe(expected: SourceExpectation, path: Path) -> Observation:
    def unreadable(detail: str, **extra: Any) -> Observation:
        return Observation(
            expected.source_id, expected.usage, expected.handling, "unreadable", detail, **extra
        )

    try:
        content = path.read_bytes()
    except OSError:
        return unreadable("file cannot be read")
    if not content or len(content) > MAX_BYTES:
        return unreadable("empty or oversized file")
    digest = hashlib.sha256(content).hexdigest()
    try:
        structure = (
            pdf_structure(content) if expected.medium == "pdf" else workbook_structure(content)
        )
    except ValueError as error:
        return unreadable(str(error), content_sha256=digest, byte_size=len(content))
    state, differences = compare(
        expected, content_sha256=digest, byte_size=len(content), structure=structure
    )
    detail = {
        "satisfied": "matches the reviewed pin",
        "unpinned": "supplied and consistent, but no reviewer pin exists yet",
        "changed": "content differs from the reviewed pin",
        "structure_differs": "declared structure differs from the supplied file",
    }[state]
    return Observation(
        expected.source_id,
        expected.usage,
        expected.handling,
        state,
        detail,
        content_sha256=digest,
        byte_size=len(content),
        structure=structure,
        differences=differences,
    )


def parse_file(value: str) -> tuple[str, Path]:
    source_id, separator, path = value.partition("=")
    if not separator or not source_id or not path:
        raise argparse.ArgumentTypeError("Use SOURCE_ID=PATH")
    return source_id, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--file",
        action="append",
        type=parse_file,
        default=[],
        metavar="SOURCE_ID=PATH",
        help="A supplied original for one declared source; repeatable",
    )
    args = parser.parse_args(argv)
    try:
        manifest = CaseSourceManifest.model_validate_json(args.manifest.read_bytes())
    except Exception:
        # Never echo manifest input or paths in a failure report.
        print(json.dumps({"schema_version": "case-source-check-v1", "status": "invalid_manifest"}))
        return 1

    supplied = dict(args.file)
    unknown = sorted(set(supplied) - {source.source_id for source in manifest.sources})
    observations: list[Observation] = []
    for expected in manifest.sources:
        path = supplied.get(expected.source_id)
        if path is None:
            observations.append(
                Observation(
                    expected.source_id,
                    expected.usage,
                    expected.handling,
                    "not_supplied",
                    "no local original was supplied for this declared source",
                )
            )
            continue
        observations.append(observe(expected, path))

    usable = {"satisfied"}
    report: dict[str, Any] = {
        "schema_version": "case-source-check-v1",
        "status": "blocked"
        if unknown or any(o.state != "satisfied" for o in observations)
        else "sources_pinned",
        "manifest_id": manifest.manifest_id,
        "manifest_digest": manifest.digest,
        "case": {
            "district": manifest.case.district,
            "effective_date": manifest.case.effective_date,
            "effective_date_as_written": manifest.case.effective_date_as_written,
            "subjects": {s.label: s.role for s in manifest.case.subjects},
        },
        "sources": [asdict(o) for o in observations],
        "unknown_source_ids": unknown,
        "not_supplied": sorted(o.source_id for o in observations if o.state == "not_supplied"),
        "unpinned": sorted(manifest.unpinned()),
        "ready_for_registry": sorted(
            o.source_id
            for o in observations
            if o.state in usable and o.source_id in {s.source_id for s in manifest.direct_sources()}
        ),
        "handling": {source.source_id: source.handling for source in manifest.sources},
        "blocking_questions": [
            {
                "question_id": q.question_id,
                "topic": q.topic,
                "owner": q.owner,
                "blocks": q.blocks,
                "source_id": q.source_id,
            }
            for q in manifest.blocking_questions()
        ],
        "rule_approval": "not_granted",
        "cloud_admission": "not_granted",
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
