"""Replay exact local evaluation inputs; write only value-free JSON and English reports."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from appraisal_review.application.extraction_evaluation import (
    EvaluationError,
    english_summary,
    run_evaluation,
    score_evaluation,
)
from appraisal_review.domain.evaluation_contracts import EvaluationManifest
from appraisal_review.domain.evaluation_report import EvaluationReplay
from appraisal_review.domain.golden_contract import GoldenCase, GoldenSuite


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError("duplicate_json_key")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def load_goldens(path: Path) -> GoldenSuite:
    """Read #23 case files or an exact GoldenSuite; never invoke a fixture generator."""
    if path.is_dir():
        return GoldenSuite(
            cases=tuple(
                GoldenCase.model_validate(_read_json(p))
                for p in sorted(path.glob("*.json"))
                if p.name != "index.json"
            )
        )
    return GoldenSuite.model_validate(_read_json(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", required=True, type=Path, help="Existing EvaluationManifest JSON"
    )
    parser.add_argument(
        "--outcomes", required=True, type=Path, help="Private EvaluationReplay JSON"
    )
    parser.add_argument(
        "--goldens", required=True, type=Path, help="#23 directory or GoldenSuite JSON"
    )
    parser.add_argument("--output-dir", required=True, type=Path, help="New local report directory")
    parser.add_argument(
        "--execute", action="store_true", help="Explicitly invoke a provider composition"
    )
    parser.add_argument(
        "--provider-factory",
        help="module:function accepting the manifest and returning an async page callback",
    )
    args = parser.parse_args(argv)
    try:
        output = args.output_dir.resolve()
        golden_path = args.goldens.resolve()
        inputs = {args.manifest.resolve(), args.outcomes.resolve(), golden_path}
        if (
            output.exists()
            or output in inputs
            or (golden_path.is_dir() and output.is_relative_to(golden_path))
        ):
            raise EvaluationError("unsafe_report_destination")
        manifest = EvaluationManifest.model_validate(_read_json(args.manifest))
        replay = EvaluationReplay.model_validate(_read_json(args.outcomes))
        goldens = load_goldens(args.goldens)
        report = score_evaluation(manifest, replay, goldens)
        if args.execute:
            if not args.provider_factory or ":" not in args.provider_factory:
                raise EvaluationError("provider_factory_required")
            if (
                manifest.execution_kind not in {"live", "mocked"}
                or replay.outcomes
                or replay.execution_errors
            ):
                raise EvaluationError("invalid_execution_seed")
            module_name, factory_name = args.provider_factory.split(":", 1)
            try:
                factory = getattr(importlib.import_module(module_name), factory_name)
                callback = factory(manifest)
                if not callable(callback):
                    raise TypeError
            except Exception:
                raise EvaluationError("provider_composition_error") from None
            replay, report = asyncio.run(run_evaluation(manifest, replay, goldens, callback))
        elif args.provider_factory:
            raise EvaluationError("execution_opt_in_required")
        # Serialize before creating the destination. Original inputs are never rewritten.
        rendered = {
            "report.json": report.model_dump_json(indent=2) + "\n",
            "summary.txt": english_summary(report),
        }
        if args.execute:
            # Re-execution evidence stays private and local; never echo it to stdout.
            rendered["private-outcomes.json"] = replay.model_dump_json(indent=2) + "\n"
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
        for name, content in rendered.items():
            descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
        print(rendered["summary.txt"], end="")
        return 0
    except EvaluationError as error:
        reason = str(error)
    except ValidationError:
        reason = "invalid_contract"
    except (json.JSONDecodeError, UnicodeError):
        reason = "invalid_json"
    except OSError:
        reason = "local_io_error"
    print(f"Evaluation rejected: {reason}.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
