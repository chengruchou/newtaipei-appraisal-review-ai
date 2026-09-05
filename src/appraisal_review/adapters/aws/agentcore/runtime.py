"""Local invocation adapter, not a deployed AgentCore Runtime or SDK server."""

import json

from pydantic import JsonValue, ValidationError

from appraisal_review.application.bootstrap import ControllerFactory, build_controller
from appraisal_review.application.entrypoint import EntryError, EntryProblem, execute_review
from appraisal_review.domain.factor_models import AgentReviewRequest


async def invoke(
    payload: object,
    *,
    controller_factory: ControllerFactory = build_controller,
) -> dict[str, JsonValue]:
    try:
        # Reject non-JSON Python objects and non-finite values rather than stringifying them.
        encoded = json.dumps(payload, allow_nan=False)
        request = AgentReviewRequest.model_validate_json(encoded)
    except (ValidationError, ValueError, TypeError, RecursionError):
        return EntryProblem(code="invalid_request", message="Invalid review request.").response()
    try:
        result = await execute_review(request, controller_factory)
    except EntryError as error:
        return error.problem.response()
    return result.model_dump(mode="json")
