"""FastAPI override point; resolves tools only after payload validation."""

from fastapi import Request

from appraisal_review.application.bootstrap import ControllerFactory


def get_controller_factory(request: Request) -> ControllerFactory:
    factory: ControllerFactory = request.app.state.controller_factory
    return factory
