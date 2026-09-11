"""Separately paired loopback preview of configured original PDFs, with no writes."""

from __future__ import annotations

import asyncio
import hmac
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from appraisal_review.adapters.local.integrated_service import LocalDirectory
from appraisal_review.adapters.local.original_documents import LocalOriginalDocuments
from appraisal_review.application.runtime_sources import source_fault
from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.domain.document_transfer import DocumentFault, DocumentOperation
from appraisal_review.domain.service_contracts import ServiceErrorCode


def create_original_preview(
    *,
    authority: str,
    origin: str,
    pairing_token: str,
    directory: LocalDirectory,
    documents: LocalOriginalDocuments,
) -> FastAPI:
    """Both the independent pairing and current case session are mandatory per read."""
    parsed = urlsplit(origin)
    if (
        not authority.startswith("127.0.0.1:")
        or parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.port is None
        or len(pairing_token) < 32
    ):
        raise ValueError("Configure exact loopback preview and browser origins with pairing")
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def boundary(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if (
            request.headers.get("host") != authority
            or request.headers.get("origin") != origin
            or request.method not in {"GET", "OPTIONS"}
        ):
            return Response(status_code=403)
        cors = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Authorization, X-Review-Session",
            "Vary": "Origin",
            "Cache-Control": "no-store",
        }
        if request.method == "OPTIONS":
            if request.headers.get("access-control-request-method") != "GET":
                return Response(status_code=403)
            return Response(status_code=204, headers=cors)
        expected = "Bearer " + pairing_token
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            return Response(status_code=403, headers=cors)
        try:
            principal = directory.authenticate(request.headers.get("x-review-session", ""))
            request.state.principal = principal
            response = await call_next(request)
        except ServiceFault as fault:
            response = JSONResponse(status_code=403, content=fault.problem.model_dump(mode="json"))
        for key, value in cors.items():
            response.headers[key] = value
        return response

    @app.exception_handler(ServiceFault)
    async def service_error(request: Request, fault: ServiceFault) -> JSONResponse:
        from appraisal_review.api.app import FAULT_STATUS

        return JSONResponse(
            status_code=FAULT_STATUS[fault.problem.code],
            content=fault.problem.model_dump(mode="json"),
        )

    @app.get("/local-original/documents/{document_id}")
    async def original(
        document_id: str, version: str, content_hash: str, request: Request
    ) -> Response:
        principal = request.state.principal
        try:
            reference = documents._reference(document_id)
            if reference.version != version or reference.content_hash != content_hash:
                raise ServiceFault(ServiceErrorCode.CONFLICT)
            data = await asyncio.to_thread(documents.read, principal, reference)
            current = directory.authenticate(request.headers.get("x-review-session", ""))
            if current != principal:
                raise ServiceFault(ServiceErrorCode.UNAUTHORIZED)
            documents.authorization.require(
                current, reference.case_id, reference.purpose, DocumentOperation.READ
            )
        except DocumentFault as fault:
            raise source_fault(fault) from None
        return Response(
            data.content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "no-store",
            },
        )

    return app
