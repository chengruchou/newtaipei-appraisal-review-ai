"""Local demo console: a visual surface over the reviewer's public HTTP API.

The console never calculates, grades or verifies anything. It forwards a request to
a configured reviewer service, returns that service's own answer unchanged, and
serves the resulting synthetic artifact for download. It is a single-operator local
tool: it has no authentication and must not be exposed beyond the demo host or
pointed at real case documents.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from appraisal_review.adapters.local.document_manifest import InputManifest

DIRECTORY = Path(os.environ.get("APPRAISAL_DEMO_DIRECTORY", "/app/artifacts/local-demo"))
STATIC = Path(__file__).resolve().parent / "static"
TIMEOUT = float(os.environ.get("APPRAISAL_CONSOLE_TIMEOUT", "120"))


@dataclass(frozen=True)
class Upstream:
    key: str
    label: str
    base_url: str
    description: str


UPSTREAMS: tuple[Upstream, ...] = (
    Upstream(
        key="reviewer",
        label="reviewer",
        base_url=os.environ.get("APPRAISAL_REVIEWER_URL", "http://reviewer:8000"),
        description="Approved synthetic material with confirmed evidence.",
    ),
    Upstream(
        key="gate",
        label="reviewer-gate",
        base_url=os.environ.get("APPRAISAL_GATE_URL", "http://reviewer-gate:8000"),
        description="The same service pinned to material whose evidence is missing.",
    ),
)
BY_KEY = {upstream.key: upstream for upstream in UPSTREAMS}


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    upstream: str
    request_file: str
    fresh_destination: bool
    expectation: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="review",
        title="Review only",
        upstream="reviewer",
        request_file="request.json",
        fresh_destination=False,
        expectation=(
            "The request asks for no artifact, so a verified run reports "
            "artifact_status not_requested and writes no file."
        ),
    ),
    Scenario(
        key="complete",
        title="Review and write the PDF",
        upstream="reviewer",
        request_file="request-write.json",
        fresh_destination=True,
        expectation=(
            "A verified run writes one new PDF into the configured output directory. "
            "Repeating the same destination is rejected; take a fresh destination first."
        ),
    ),
    Scenario(
        key="blocked",
        title="Missing evidence is blocked",
        upstream="gate",
        request_file="request-write.json",
        fresh_destination=True,
        expectation=(
            "The pinned material drops the target evidence, so the run stays "
            "needs_review and no PDF is written."
        ),
    ),
)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str
    payload: dict[str, Any]


def problem(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def output_directory() -> Path:
    return DIRECTORY / "output"


def fresh_destination() -> str:
    return (output_directory() / f"completed-{uuid.uuid4().hex[:12]}.pdf").as_uri()


def scenario_payload(scenario: Scenario) -> dict[str, Any] | None:
    path = DIRECTORY / scenario.request_file
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if scenario.fresh_destination and payload.get("output_pdf_uri"):
        payload["output_pdf_uri"] = fresh_destination()
    return payload


def create_app() -> FastAPI:
    app = FastAPI(
        title="Appraisal review demo console",
        version="0.1.0",
        description="Local visualization of the synthetic reviewer pipeline.",
    )

    @app.get("/console/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "fixture_ready": (DIRECTORY / "config.json").is_file()}

    @app.get("/console/upstreams")
    async def upstreams() -> dict[str, object]:
        results = []
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            for upstream in UPSTREAMS:
                entry: dict[str, object] = {
                    "key": upstream.key,
                    "label": upstream.label,
                    "base_url": upstream.base_url,
                    "description": upstream.description,
                }
                try:
                    response = await client.get(f"{upstream.base_url}/health")
                    entry["reachable"] = response.status_code == 200
                    entry["body"] = response.json()
                except (httpx.HTTPError, ValueError) as error:
                    entry["reachable"] = False
                    entry["body"] = {"error": type(error).__name__}
                results.append(entry)
        return {"upstreams": results}

    @app.get("/console/scenarios")
    async def scenarios() -> dict[str, object]:
        listed = []
        for scenario in SCENARIOS:
            upstream = BY_KEY[scenario.upstream]
            listed.append(
                {
                    "key": scenario.key,
                    "title": scenario.title,
                    "upstream": scenario.upstream,
                    "upstream_label": upstream.label,
                    "url": f"{upstream.base_url}/v1/reviews",
                    "expectation": scenario.expectation,
                    "fresh_destination": scenario.fresh_destination,
                    "payload": scenario_payload(scenario),
                }
            )
        return {"scenarios": listed, "fixture_ready": (DIRECTORY / "config.json").is_file()}

    @app.post("/console/run")
    async def run(request: RunRequest) -> JSONResponse:
        selected = next((s for s in SCENARIOS if s.key == request.scenario), None)
        if selected is None:
            return problem(422, "unknown_scenario", "No such demo scenario.")
        upstream = BY_KEY[selected.upstream]
        url = f"{upstream.base_url}/v1/reviews"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False) as client:
                response = await client.post(url, json=request.payload)
        except httpx.HTTPError as error:
            return problem(502, "upstream_unreachable", f"{type(error).__name__} calling {url}.")
        elapsed = round((time.monotonic() - started) * 1000)
        try:
            body: Any = response.json()
        except ValueError:
            body = {"raw": response.text[:2000]}
        return JSONResponse(
            content={
                "upstream": upstream.label,
                "url": url,
                "status_code": response.status_code,
                "elapsed_ms": elapsed,
                "body": body,
            }
        )

    @app.get("/console/artifact")
    async def artifact(uri: str) -> Any:
        prefix = "file://"
        if not uri.startswith(prefix):
            return problem(422, "unsupported_uri", "Only local file URIs are served.")
        candidate = Path(uri[len(prefix) :]).resolve()
        root = output_directory().resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return problem(404, "artifact_not_found", "No artifact at that configured path.")
        return FileResponse(candidate, media_type="application/pdf", filename=candidate.name)

    @app.get("/console/page")
    async def page(document_id: str, number: int = 1, scale: float = 2.0) -> JSONResponse:
        """Render one allowlisted source page so a citation can be seen, not just read.

        The manifest parser enforces the configured allowlist and the recorded content
        hash, so this cannot render a document the reviewed material did not pin.
        """
        try:
            manifest = InputManifest.model_validate_json((DIRECTORY / "inputs.json").read_bytes())
        except (OSError, ValueError):
            return problem(503, "fixture_unavailable", "The synthetic input manifest is absent.")
        spec = next((d for d in manifest.documents if d.document_id == document_id), None)
        if spec is None:
            return problem(404, "unknown_document", "No such allowlisted source document.")
        if not 1 <= number <= 200 or not 0.5 <= scale <= 3.0:
            return problem(422, "out_of_range", "Page or scale outside the supported range.")
        parser = manifest.parser()
        uri = spec.path.resolve().as_uri()
        try:
            parsed = await parser.parse_document(uri)
            image = await parser.render(uri, number, scale=scale)
        except (ValueError, OSError) as error:
            return problem(422, "render_failed", f"{type(error).__name__} rendering the source.")
        source = parsed.source
        if source is None or not 1 <= number <= len(source.pages):
            return problem(404, "page_not_found", "That page is outside the source document.")
        rendered = source.pages[number - 1]
        return JSONResponse(
            content={
                "document_id": document_id,
                "version": spec.version,
                "role": spec.role,
                "page": number,
                "width": rendered.width,
                "height": rendered.height,
                "scale": scale,
                "coordinate_system": "pdf_bottom_left",
                "image": "data:image/png;base64," + base64.b64encode(image).decode(),
            }
        )

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


app = create_app()
