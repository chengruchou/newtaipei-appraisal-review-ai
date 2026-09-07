"""Local HTTP factory and invocation CLI; configuration is an operator-owned file."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI

from appraisal_review.adapters.aws.agentcore.runtime import invoke
from appraisal_review.adapters.local.service import load_service
from appraisal_review.api.app import create_app
from appraisal_review.application.bootstrap import ConfigurationError
from appraisal_review.application.controller import ReviewAgentController
from appraisal_review.domain.factor_models import AgentReviewRequest


def app_from_environment() -> FastAPI:
    """uvicorn --factory entry. Request validation precedes lazy configuration I/O."""

    def factory() -> ReviewAgentController:
        name = os.environ.get("APPRAISAL_LOCAL_CONFIG")
        if not name:
            raise ConfigurationError("missing_local_service_configuration")
        return load_service(Path(name)).controller_factory()

    return create_app(controller_factory=factory)


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("command", choices=["invoke", "run"])
    cli.add_argument("--config", type=Path, required=True)
    cli.add_argument("--request", type=Path, required=True)
    args = cli.parse_args()
    # Keep the JSON stdout channel free of the parser's optional package advertisement.
    os.environ["PYMUPDF_SUGGEST_LAYOUT_ANALYZER"] = "0"
    os.environ["PYMUPDF_MESSAGE"] = "fd:2"
    try:
        payload = json.loads(args.request.read_bytes())
        if args.command == "invoke":
            result = asyncio.run(
                invoke(
                    payload,
                    controller_factory=lambda: load_service(args.config).controller_factory(),
                )
            )
            print(json.dumps(result, ensure_ascii=False))
        else:
            result_model = asyncio.run(
                load_service(args.config).run(AgentReviewRequest.model_validate(payload))
            )
            print(result_model.model_dump_json())
    except Exception:
        print(
            '{"error":{"code":"invalid_local_request","message":"Local command failed."}}',
            file=sys.stderr,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
