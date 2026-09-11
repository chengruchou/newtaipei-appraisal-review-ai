"""Prepare unapproved local material from pinned originals and controlled selections.

Run with PYTHONPATH=$PWD/src .venv/bin/python scripts/prepare_local_case.py.
All four JSON inputs and an explicit authorized source root are required. Outputs
are exclusive private files under the current worktree's artifacts directory.
Exit 0 means candidate preparation succeeded, never that a case was accepted.
Exit 2 means no rule bundle could be resolved; inspect rule-resolution.json.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from appraisal_review.adapters.local.case_preparation import (
    CandidateSelections,
    prepare_case,
    read_local_json,
    write_prepared_case,
)
from appraisal_review.adapters.local.document_manifest import InputManifest
from appraisal_review.domain.document_models import SourceRegistry
from appraisal_review.domain.rule_sources import RuleCatalog


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "registry", "catalog", "selections", "output"):
        cli.add_argument(f"--{name}", type=Path, required=True)
    cli.add_argument("--source-root", type=Path, action="append", required=True)
    cli.add_argument("--max-bytes", type=int, default=100_000_000)
    cli.add_argument("--max-pages", type=int, default=200)
    args = cli.parse_args()
    try:
        manifest = InputManifest.model_validate_json(read_local_json(args.manifest))
        registry = SourceRegistry.model_validate_json(read_local_json(args.registry))
        catalog = RuleCatalog.model_validate_json(read_local_json(args.catalog))
        selections = CandidateSelections.model_validate_json(read_local_json(args.selections))
        prepared = prepare_case(
            manifest,
            registry,
            catalog,
            selections,
            source_roots=args.source_root,
            max_bytes=args.max_bytes,
            max_pages=args.max_pages,
        )
        write_prepared_case(
            args.output,
            workspace=Path.cwd(),
            prepared=prepared,
            manifest=manifest,
            registry=registry,
            catalog=catalog,
            selections=selections,
        )
    except (ValueError, OSError) as error:
        cli.exit(2, f"Preparation rejected: {error}\n")
    print(f"Candidate preparation: {prepared.resolution.status}; approval and conditions pending")
    print(f"Private output: {args.output.resolve()}")
    return 0 if prepared.material is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
