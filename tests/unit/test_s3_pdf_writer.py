from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
import reportlab
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from appraisal_review.adapters.aws.pdf.s3_pdf_writer import S3PDFWriter
from appraisal_review.adapters.aws.storage.s3_object_store import S3Location, S3ObjectStore
from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    field_map_sha256,
)
from appraisal_review.adapters.local.pdf_writer import LocalPDFWriter
from appraisal_review.domain.factor_models import (
    EvaluationStatus,
    EvaluationSummary,
    FactorEvaluationResult,
    FactorReviewResult,
    Grade,
)
from appraisal_review.domain.pdf_models import (
    InvalidPDFResultError,
    PDFField,
    PDFFieldMap,
    PDFFontError,
    PDFReadError,
    PDFValueRef,
    PDFWriteError,
    PDFWriteRequest,
    PDFWriteResult,
    UnsupportedDocumentURIError,
)

SOURCE_URI = "s3://input-bucket/cases//2026/../來源.pdf"
OUTPUT_URI = "s3://result-bucket/reviews//2026/../完成.pdf"


def source_pdf() -> bytes:
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(320, 220), invariant=1)
    canvas.drawString(20, 190, "UNCHANGED")
    canvas.save()
    return stream.getvalue()


def review_result() -> FactorReviewResult:
    return FactorReviewResult(
        case_id="case-1",
        rule_set_id="rules-1",
        rule_version="1",
        results=[
            FactorEvaluationResult(
                factor_id="road.width",
                target_grade=Grade.EXCELLENT,
                comparable_grade=Grade.NORMAL,
                adjustment_percent=5.0,
                rule_id="road.width.v1",
                calculation_trace="synthetic",
                status=EvaluationStatus.VERIFIED,
            )
        ],
        summary=EvaluationSummary(
            total_adjustment_percent=5.0,
            status=EvaluationStatus.VERIFIED,
        ),
    )


def request() -> PDFWriteRequest:
    return PDFWriteRequest(
        source_uri=SOURCE_URI,
        destination_uri=OUTPUT_URI,
        result=review_result(),
        field_map=PDFFieldMap(
            template_id="synthetic-v1",
            fields=[
                PDFField(
                    field_id="road-rate",
                    page=1,
                    bounding_box=(100, 100, 200, 130),
                    operation="fill_blank",
                    value_ref=PDFValueRef(
                        scope="regional",
                        target_id="target",
                        comparable_id="comparison-1",
                        factor_id="road.width",
                        value="adjustment_percent",
                    ),
                )
            ],
        ),
    )


class MemoryS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {
            ("input-bucket", "cases//2026/../來源.pdf"): source_pdf()
        }
        self.events: list[tuple[Any, ...]] = []
        self.local_paths: list[Path] = []
        self.download_error: Exception | None = None
        self.upload_error: Exception | None = None

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        self.events.append(("download", Bucket, Key))
        self.local_paths.append(Path(Filename))
        if self.download_error is not None:
            raise self.download_error
        Path(Filename).write_bytes(self.objects[(Bucket, Key)])

    def put_object(self, **kwargs: Any) -> None:
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        body = kwargs["Body"]
        options = {
            name: value for name, value in kwargs.items() if name not in {"Bucket", "Key", "Body"}
        }
        self.events.append(("upload", bucket, key, options))
        self.local_paths.append(Path(body.name))
        if self.upload_error is not None:
            raise self.upload_error
        if kwargs.get("IfNoneMatch") == "*" and (bucket, key) in self.objects:
            raise FileExistsError("synthetic conditional write conflict")
        self.objects[(bucket, key)] = body.read()


class RecordingLocalWriter:
    def __init__(self, delegate: LocalPDFWriter, events: list[tuple[Any, ...]]) -> None:
        self.delegate = delegate
        self.events = events

    async def write_pdf(self, write_request: PDFWriteRequest) -> PDFWriteResult:
        self.events.append(("local_write",))
        result = await self.delegate.write_pdf(write_request)
        self.events.append(("local_validated",))
        return result


class FailingLocalWriter:
    async def write_pdf(self, _request: PDFWriteRequest) -> PDFWriteResult:
        raise PDFFontError("synthetic local validation failure")


class NonWritingLocalWriter:
    async def write_pdf(self, write_request: PDFWriteRequest) -> PDFWriteResult:
        return PDFWriteResult(
            output_uri=write_request.destination_uri,
            page_count=1,
            written_field_ids=["road-rate"],
        )


def local_writer() -> LocalPDFWriter:
    font = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    field_map = request().field_map
    return LocalPDFWriter(
        render_config=PDFRenderConfig(font_path=font),
        template_policy=PDFTemplatePolicy(
            template_id="synthetic-v1",
            template_sha256=hashlib.sha256(source_pdf()).hexdigest(),
            field_map_sha256=field_map_sha256(field_map),
            editable_pages=frozenset({1}),
        ),
    )


def test_s3_writer_downloads_validates_then_uploads_literal_keys() -> None:
    client = MemoryS3Client()
    writer = S3PDFWriter(
        object_store=S3ObjectStore(client),
        local_writer=RecordingLocalWriter(local_writer(), client.events),
    )

    result = asyncio.run(writer.write_pdf(request()))

    assert [event[0] for event in client.events] == [
        "download",
        "local_write",
        "local_validated",
        "upload",
    ]
    assert client.events[0] == ("download", "input-bucket", "cases//2026/../來源.pdf")
    assert client.events[-1] == (
        "upload",
        "result-bucket",
        "reviews//2026/../完成.pdf",
        {"ContentType": "application/pdf", "IfNoneMatch": "*"},
    )
    assert result == PDFWriteResult(
        output_uri=OUTPUT_URI,
        page_count=1,
        written_field_ids=["road-rate"],
    )
    uploaded = client.objects[("result-bucket", "reviews//2026/../完成.pdf")]
    assert "+5.00%" in PdfReader(BytesIO(uploaded)).pages[0].extract_text()
    assert all(not path.exists() for path in client.local_paths)


def test_s3_writer_rechecks_protected_destination_before_transfer() -> None:
    client = MemoryS3Client()
    writer = S3PDFWriter(
        object_store=S3ObjectStore(client),
        local_writer=RecordingLocalWriter(local_writer(), client.events),
    )
    write_request = request()
    write_request.protected_source_uris = [OUTPUT_URI]

    with pytest.raises(PDFWriteError, match="protected reviewed source"):
        asyncio.run(writer.write_pdf(write_request))

    assert client.events == []


def test_local_failure_suppresses_upload_and_cleans_temporary_files() -> None:
    client = MemoryS3Client()
    writer = S3PDFWriter(
        object_store=S3ObjectStore(client),
        local_writer=FailingLocalWriter(),
    )

    with pytest.raises(PDFFontError, match="local validation failure"):
        asyncio.run(writer.write_pdf(request()))

    assert [event[0] for event in client.events] == ["download"]
    assert all(not path.exists() for path in client.local_paths)
    assert ("result-bucket", "reviews//2026/../完成.pdf") not in client.objects


def test_missing_local_artifact_suppresses_upload() -> None:
    client = MemoryS3Client()
    writer = S3PDFWriter(
        object_store=S3ObjectStore(client),
        local_writer=NonWritingLocalWriter(),
    )

    with pytest.raises(InvalidPDFResultError, match="inconsistent"):
        asyncio.run(writer.write_pdf(request()))

    assert [event[0] for event in client.events] == ["download"]


def test_upload_failure_cleans_local_files_and_returns_no_result() -> None:
    client = MemoryS3Client()
    client.upload_error = RuntimeError("synthetic upload failure")
    writer = S3PDFWriter(
        object_store=S3ObjectStore(client),
        local_writer=local_writer(),
    )

    with pytest.raises(PDFWriteError, match="could not be uploaded"):
        asyncio.run(writer.write_pdf(request()))

    assert [event[0] for event in client.events] == ["download", "upload"]
    assert all(not path.exists() for path in client.local_paths)
    assert ("result-bucket", "reviews//2026/../完成.pdf") not in client.objects


def test_existing_s3_destination_is_preserved_by_default(tmp_path: Path) -> None:
    client = MemoryS3Client()
    client.objects[("result-bucket", "reviews//2026/../完成.pdf")] = b"existing"
    local_output = tmp_path / "output.pdf"
    local_output.write_bytes(b"replacement")

    with pytest.raises(PDFWriteError, match="could not be uploaded"):
        S3ObjectStore(client).upload_pdf(local_output, OUTPUT_URI)

    assert client.objects[("result-bucket", "reviews//2026/../完成.pdf")] == b"existing"


def test_explicit_s3_overwrite_replaces_destination(tmp_path: Path) -> None:
    client = MemoryS3Client()
    client.objects[("result-bucket", "reviews//2026/../完成.pdf")] = b"existing"
    local_output = tmp_path / "output.pdf"
    local_output.write_bytes(b"replacement")

    S3ObjectStore(client, overwrite_existing=True).upload_pdf(local_output, OUTPUT_URI)

    assert client.events[-1] == (
        "upload",
        "result-bucket",
        "reviews//2026/../完成.pdf",
        {"ContentType": "application/pdf"},
    )
    assert client.objects[("result-bucket", "reviews//2026/../完成.pdf")] == b"replacement"


def test_transfer_failures_use_stable_pdf_error_types(tmp_path: Path) -> None:
    client = MemoryS3Client()
    store = S3ObjectStore(client)
    client.download_error = RuntimeError("credential detail")
    with pytest.raises(PDFReadError, match="could not be downloaded"):
        store.download(SOURCE_URI, tmp_path / "source.pdf")

    local_output = tmp_path / "output.pdf"
    local_output.write_bytes(b"%PDF-1.7\nvalidated")
    client.upload_error = RuntimeError("credential detail")
    with pytest.raises(PDFWriteError, match="could not be uploaded"):
        store.upload_pdf(local_output, OUTPUT_URI)


def test_s3_boundaries_reject_non_s3_uris(tmp_path: Path) -> None:
    client = MemoryS3Client()
    store = S3ObjectStore(client)
    local_uri = (tmp_path / "source.pdf").as_uri()

    with pytest.raises(UnsupportedDocumentURIError, match="requires an S3 URI"):
        S3Location.from_uri(local_uri)
    with pytest.raises(UnsupportedDocumentURIError, match="requires S3 object URIs"):
        S3PDFWriter(object_store=store, local_writer=FailingLocalWriter()).object_store_location(
            local_uri
        )
    assert client.events == []
