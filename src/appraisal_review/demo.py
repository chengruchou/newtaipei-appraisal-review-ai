"""Run the explicitly synthetic workflow without credentials or source documents."""

import argparse
import asyncio
import json
import sys

from appraisal_review.adapters.aws.agentcore.runtime import invoke
from appraisal_review.adapters.local.synthetic import synthetic_request
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["verified", "completed", "needs_review"])
    args = parser.parse_args()
    print("SYNTHETIC DEMO: no document analysis or PDF creation.", file=sys.stderr)
    request = synthetic_request(args.scenario)
    result = asyncio.run(
        invoke(
            request.model_dump(mode="json"),
            controller_factory=lambda: build_controller(
                Settings(runtime_mode="local", synthetic_demo=True)
            ),
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    if "error" in result:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
