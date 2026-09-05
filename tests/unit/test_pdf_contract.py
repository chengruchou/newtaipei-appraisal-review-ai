import asyncio
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from appraisal_review.domain.factor_models import (
    AgentReviewRun,
    EvaluationStatus,
    EvaluationSummary,
    FactorEvaluationResult,
    FactorReviewResult,
    Grade,
)
from appraisal_review.domain.factor_models import (
    PDFField as LegacyPDFField,
)
from appraisal_review.domain.pdf_models import (
    PDFField,
    PDFFieldMap,
    PDFValueRef,
    PDFWriteRequest,
    PDFWriteResult,
    document_identity,
)
from appraisal_review.ports.pdf import PDFWriter
from appraisal_review.ports.workflow import PDFWriter as LegacyPDFWriter


def write_request() -> PDFWriteRequest:
    return PDFWriteRequest(
        source_uri="file:///synthetic/input.pdf",
        destination_uri="file:///synthetic/output.pdf",
        result=FactorReviewResult(
            case_id="synthetic",
            rule_set_id="synthetic",
            rule_version="1",
            results=[
                FactorEvaluationResult(
                    factor_id="road.width",
                    target_grade=Grade.NORMAL,
                    comparable_grade=Grade.NORMAL,
                    adjustment_percent=0.0,
                    rule_id="road.v1",
                    calculation_trace="synthetic",
                    status=EvaluationStatus.VERIFIED,
                )
            ],
            summary=EvaluationSummary(
                total_adjustment_percent=0.0, status=EvaluationStatus.VERIFIED
            ),
        ),
        field_map=PDFFieldMap(
            template_id="synthetic",
            fields=[
                PDFField(
                    field_id="road.rate",
                    page=1,
                    bounding_box=(1, 2, 30, 40),
                    value_ref=PDFValueRef(
                        scope="regional",
                        target_id="t",
                        comparable_id="c",
                        factor_id="road.width",
                        value="adjustment_percent",
                    ),
                )
            ],
        ),
    )


def test_single_contract_and_json_roundtrip() -> None:
    assert LegacyPDFField is PDFField
    assert LegacyPDFWriter is PDFWriter
    request = write_request()
    assert PDFWriteRequest.model_validate_json(request.model_dump_json()) == request
    # Both import orders must work in a fresh interpreter, not just pytest's cached modules.
    for module in ("factor_models", "pdf_models"):
        subprocess.run(
            [
                sys.executable,
                "-c",
                f"import appraisal_review.domain.{module}; "
                "from appraisal_review.domain.factor_models import AgentReviewRequest; "
                "AgentReviewRequest.model_json_schema()",
            ],
            check=True,
        )


@pytest.mark.parametrize(
    "uri",
    [
        "x.pdf",
        "https://host/x.pdf",
        "file://remote/x.pdf",
        "s3://b/",
        "s3://b/x?version=1",
        "s3://b/x#",
        "s3://b/%2Fx",
        "file:///%00",
    ],
)
def test_unsupported_uri(uri: str) -> None:
    with pytest.raises(ValueError):
        document_identity(uri)


def test_file_alias_conflict_and_s3_key_semantics() -> None:
    payload = write_request().model_dump()
    payload["destination_uri"] = "file://localhost/synthetic/../synthetic/input.pdf"
    with pytest.raises(ValidationError, match="must differ"):
        PDFWriteRequest.model_validate(payload)
    assert document_identity("s3://bucket/a/../b.pdf") != document_identity("s3://bucket/b.pdf")


@pytest.mark.parametrize(
    "change",
    [
        {"page": 0},
        {"page": True},
        {"page": "1"},
        {"unexpected": 1},
        {"bounding_box": [0, 0, 0, 10]},
        {"bounding_box": [-1, 0, 10, 10]},
        {"bounding_box": [0, 0, float("nan"), 10]},
    ],
)
def test_invalid_placement_contract(change: dict[str, object]) -> None:
    field = write_request().field_map.fields[0].model_dump()
    with pytest.raises(ValidationError):
        PDFField.model_validate(field | change)


def test_duplicate_and_ambiguous_bindings_fail() -> None:
    request = write_request()
    payload = request.model_dump()
    payload["field_map"]["fields"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        PDFWriteRequest.model_validate(payload)
    payload = request.model_dump()
    payload["result"]["results"] *= 2
    with pytest.raises(ValidationError, match="exactly once"):
        PDFWriteRequest.model_validate(payload)
    payload = request.model_dump()
    payload["field_map"]["fields"][0]["value_ref"] = None
    with pytest.raises(ValidationError, match="explicit value"):
        PDFWriteRequest.model_validate(payload)


def test_fake_writer_preserves_metadata_without_writing_a_file() -> None:
    from appraisal_review.adapters.local.fake_pdf import FakePDFWriter

    request = write_request()
    writer = FakePDFWriter()
    result = asyncio.run(writer.write_pdf(request))
    assert writer.calls == [request]
    assert result.warnings == ["Synthetic PDF writer: no file was created."]
    run = AgentReviewRun(
        case_id="synthetic", status="completed", pdf_result=result, output_pdf_uri=result.output_uri
    )
    restored = AgentReviewRun.model_validate_json(run.model_dump_json())
    assert restored.pdf_result == result
    assert json.loads(run.model_dump_json())["pdf_error"] is None


@pytest.mark.parametrize(
    "change",
    [{"page_count": 0}, {"page_count": "1"}, {"written_field_ids": ["a", "a"]}, {"output_uri": ""}],
)
def test_invalid_write_result(change: dict[str, object]) -> None:
    data = dict(output_uri="file:///output.pdf", page_count=1, written_field_ids=["a"], warnings=[])
    with pytest.raises(ValidationError):
        PDFWriteResult.model_validate(data | change)
