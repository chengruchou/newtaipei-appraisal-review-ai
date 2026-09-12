"""The content plane belongs to the published contract, not to one composition root.

Both download routes used to be declared inside create_integrated_service, so
scripts/export_openapi.py - which composes create_app - produced a document that did not
mention them. The drift check passed while the browser client was missing the only routes
that deliver bytes. These tests keep the routes in the exported document and keep an
unwired plane answering 503 rather than 404.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from appraisal_review.api.app import create_app
from appraisal_review.application.service_guards import Principal
from appraisal_review.ports.content import DeliveredContent

SOURCE_PATH = "/v1/documents/{document_id}/content"
ARTIFACT_PATH = "/v1/review-jobs/{job_id}/artifacts/{artifact_id}/content"
PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def document() -> dict[str, Any]:
    return create_app().openapi()


class TestPublishedContract:
    @pytest.mark.parametrize("path", [SOURCE_PATH, ARTIFACT_PATH])
    def test_content_routes_are_in_the_exported_document(self, path: str) -> None:
        assert path in document()["paths"]

    @pytest.mark.parametrize("path", [SOURCE_PATH, ARTIFACT_PATH])
    def test_binary_media_types_are_declared(self, path: str) -> None:
        # openapi-typescript models the response from these, so a missing media type here
        # means the browser client cannot describe a download at all.
        content = document()["paths"][path]["get"]["responses"]["200"]["content"]
        assert PDF in content
        assert XLSX in content, "the official spreadsheet output must be describable"

    @pytest.mark.parametrize("path", [SOURCE_PATH, ARTIFACT_PATH])
    def test_problem_responses_are_declared(self, path: str) -> None:
        responses = document()["paths"][path]["get"]["responses"]
        for status in ("403", "404", "503"):
            assert status in responses, f"{path} must document {status}"

    def test_the_source_route_requires_an_exact_version_and_hash(self) -> None:
        names = {p["name"] for p in document()["paths"][SOURCE_PATH]["get"]["parameters"]}
        assert {"version", "content_hash"} <= names


class TestUnwiredPlane:
    """A composition with no content plane must not look like a missing artifact."""

    def test_source_read_reports_capability_unavailable(self) -> None:
        client = TestClient(create_app())
        response = client.get(
            SOURCE_PATH.format(document_id=uuid4()),
            params={"version": "v1", "content_hash": "0" * 64},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "capability_unavailable"

    def test_artifact_read_reports_capability_unavailable(self) -> None:
        client = TestClient(create_app())
        response = client.get(ARTIFACT_PATH.format(job_id=uuid4(), artifact_id=uuid4()))
        assert response.status_code == 503
        assert response.json()["code"] == "capability_unavailable"

    def test_a_plane_without_an_authenticator_is_refused(self) -> None:
        class Plane:
            async def read_source(self, principal: Principal, **kwargs: Any) -> DeliveredContent:
                raise AssertionError("never reached")

            async def read_artifact(self, principal: Principal, **kwargs: Any) -> DeliveredContent:
                raise AssertionError("never reached")

        # Content is case-scoped, so mounting it unauthenticated would publish other
        # principals' artifacts to anyone who can reach the port.
        with pytest.raises(ValueError, match="principal resolver"):
            create_app(content_plane=Plane())


class TestDeliveredContent:
    def test_empty_bytes_are_refused(self) -> None:
        with pytest.raises(ValueError, match="must carry bytes"):
            DeliveredContent(data=b"", media_type=PDF)

    @pytest.mark.parametrize("name", ["../escape.pdf", "dir/file.pdf", "back\\slash.pdf"])
    def test_a_filename_with_a_separator_is_refused(self, name: str) -> None:
        # The value reaches Content-Disposition; a separator invites a traversal reading.
        with pytest.raises(ValueError, match="path separators"):
            DeliveredContent(data=b"%PDF-1.3", media_type=PDF, filename=name)

    def test_the_spreadsheet_media_type_is_allowed(self) -> None:
        delivered = DeliveredContent(data=b"PK\x03\x04", media_type=XLSX, filename="table-3.xlsx")
        assert delivered.media_type == XLSX

    def test_uuid_named_download_is_accepted(self) -> None:
        artifact_id = UUID(int=1)
        delivered = DeliveredContent(
            data=b"%PDF-1.3", media_type=PDF, filename=f"{artifact_id}.pdf"
        )
        assert delivered.filename == f"{artifact_id}.pdf"
