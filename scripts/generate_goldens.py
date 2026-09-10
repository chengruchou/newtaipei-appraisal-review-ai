"""Write or check the reviewed full-case golden manifests.

The manifests under `tests/goldens` are review artifacts, not captured output. Running
with `--write` regenerates them from the fixture generator; every resulting change must
be re-reviewed and, where reviewers disagreed, carry an adjudication record. Evaluation
runs read these files and must never write them back.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from appraisal_review.adapters.local.golden_cases import (
    golden_fixtures,
    manifest_documents,
)
from appraisal_review.domain.golden_contract import GoldenSuite
from appraisal_review.domain.golden_validator import verify_manifest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = REPOSITORY_ROOT / "tests" / "goldens"


def rederive() -> tuple[GoldenSuite, list[str]]:
    """Re-derive every expected value from its fixture before it may be written."""
    fixtures = golden_fixtures()
    problems = []
    for fixture in fixtures:
        report = verify_manifest(fixture.case, fixture.material)
        if not report.ok:
            problems.append(report.describe())
    return GoldenSuite(cases=tuple(f.case for f in fixtures)), problems


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    cli.add_argument(
        "--write",
        action="store_true",
        help="rewrite the reviewed manifests; the diff needs human review",
    )
    args = cli.parse_args(argv)
    suite, problems = rederive()
    if problems:
        print("Manifests do not follow from their fixtures:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 2
    documents = manifest_documents(suite)
    if args.write:
        args.directory.mkdir(parents=True, exist_ok=True)
        for name in sorted({p.name for p in args.directory.glob("*.json")} - set(documents)):
            (args.directory / name).unlink()
        for name, content in sorted(documents.items()):
            (args.directory / name).write_text(content, encoding="utf-8")
        print(f"Wrote {len(documents)} golden manifests. Review the diff before committing.")
        return 0
    drift = [
        name
        for name, content in sorted(documents.items())
        if not (args.directory / name).exists()
        or (args.directory / name).read_text(encoding="utf-8") != content
    ]
    extra = sorted({p.name for p in args.directory.glob("*.json")} - set(documents))
    if drift or extra:
        print("Reviewed golden manifests differ from the fixture generator:", file=sys.stderr)
        for name in [*drift, *extra]:
            print(f"  {name}", file=sys.stderr)
        print("Run scripts/generate_goldens.py --write and review the diff.", file=sys.stderr)
        return 1
    print(f"{len(documents)} golden manifests match their fixtures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
