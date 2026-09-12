"""Check that a converted official table matches the workbook that was delivered.

Run with PYTHONPATH=$PWD/src .venv/bin/python scripts/check_converted_pdf.py
  --pdf artifacts/table4.pdf --source artifacts/table4-filled.xlsx
  --converter 'soffice 25.2' --expected-pages 1
  --require-text '<closing note from the form>' --forbid-text '<legacy example marker>'

The source digest is computed from the workbook this command is pointed at. That
records which bytes the operator says were converted; no PDF proves its own
origin, so an independent check means reconverting from the pinned source.
Exit 0 means the check ran and reported its findings. Exit 2 means the output
could not be inspected. Exit 3 means findings were reported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from appraisal_review.adapters.local.converted_pdf import (
    ConversionInspectionError,
    inspect_converted_pdf,
)
from appraisal_review.domain.workbook_conversion import ConversionExpectation, verify_conversion


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pdf", type=Path, required=True)
    source = cli.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--source-digest")
    cli.add_argument("--converter", required=True)
    cli.add_argument("--expected-pages", type=int)
    cli.add_argument("--require-text", action="append", default=[])
    cli.add_argument("--forbid-text", action="append", default=[])
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()

    try:
        digest = _digest(args.source) if args.source is not None else args.source_digest
        expectation = ConversionExpectation(
            source_name=args.source.name if args.source is not None else "declared source",
            source_digest=digest,
            expected_page_count=args.expected_pages,
            required_text=tuple(args.require_text),
            forbidden_text=tuple(args.forbid_text),
        )
        record = inspect_converted_pdf(
            args.pdf,
            source_digest=digest,
            converter=args.converter,
            probes=tuple(args.require_text) + tuple(args.forbid_text),
        )
    except (ConversionInspectionError, OSError, ValueError) as error:
        print(f"cannot inspect {args.pdf}: {error}")
        return 2

    findings = verify_conversion(expectation, record)
    print(f"{record.output_name} sha256={record.output_digest} bytes={record.output_byte_size}")
    print(f"  source={expectation.source_name} sha256={expectation.source_digest}")
    print(f"  converter={record.converter} pages={len(record.pages)} encrypted={record.encrypted}")
    for page in record.pages:
        print(
            f"  page {page.number} {page.width:.1f}x{page.height:.1f} "
            f"rotation={page.rotation} characters={page.character_count}"
        )
    for font in record.fonts:
        state = "embedded" if font.embedded else "NOT EMBEDDED"
        print(f"  font {font.name} {state} subset={font.subset} pages={len(font.pages)}")
    for finding in findings:
        print(f"  finding {finding.code}: {finding.detail}")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "expectation": expectation.model_dump(mode="json"),
                    "record": record.model_dump(mode="json"),
                    "findings": [finding.model_dump(mode="json") for finding in findings],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"report written to {args.output}")
    return 3 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
