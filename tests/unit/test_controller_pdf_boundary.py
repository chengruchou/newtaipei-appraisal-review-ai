import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from appraisal_review.adapters.local.synthetic import synthetic_adapters, synthetic_request
from appraisal_review.application.bootstrap import build_controller
from appraisal_review.config import Settings
from appraisal_review.domain.factor_models import EvaluationStatus, WorkflowStatus
from appraisal_review.domain.pdf_models import PDFErrorCode, PDFReadError, PDFWriteResult


def run(adapters, request=None):
    return asyncio.run(
        build_controller(Settings(_env_file=None), adapters=adapters).review(
            request or synthetic_request("completed")
        )
    )


@pytest.mark.parametrize(
    "condition",
    ["candidate", "missing_evidence", "low_confidence", "missing_factor", "unknown_factor"],
)
def test_actual_engine_and_verifier_block_writer(condition: str) -> None:
    adapters = synthetic_adapters()
    request = synthetic_request("needs_review" if condition == "missing_evidence" else "completed")
    parser = adapters.parser
    parser.parse_document = AsyncMock(wraps=parser.parse_document)
    extractor = adapters.fact_extractor
    extractor.extract_facts = AsyncMock(wraps=extractor.extract_facts)
    if condition == "candidate":
        rules = asyncio.run(
            adapters.rule_provider.load_or_build_rules(
                asyncio.run(parser.parse_document(request.criteria_document_uri))
            )
        )
        adapters.rule_provider.load_or_build_rules = AsyncMock(
            return_value=rules.rule_sets[0].rules.model_copy(update={"status": "candidate"})
        )
        parser.parse_document.reset_mock()
    elif condition in {"low_confidence", "missing_factor", "unknown_factor"}:
        factors = asyncio.run(
            extractor.extract_facts(
                asyncio.run(parser.parse_document(request.case_document_uri)),
                case_id=request.case_id,
            )
        )
        if condition == "low_confidence":
            factors.pairs[0].pair.target.confidence = 0.2
        elif condition == "missing_factor":
            factors.pairs = []
        else:
            factors.pairs[0].pair.factor_id = "unknown"
        extractor.extract_facts = AsyncMock(return_value=factors)
    result = run(adapters, request)
    assert result.status in {WorkflowStatus.FAILED, WorkflowStatus.NEEDS_REVIEW}
    assert not adapters.pdf_writer.calls
    assert result.output_pdf_uri is None
    if condition == "candidate":
        extractor.extract_facts.assert_not_called()
        parser.parse_document.assert_awaited_once_with(request.criteria_document_uri)
    elif condition == "unknown_factor":
        assert result.case_review.status is EvaluationStatus.FAILED


def test_success_writes_once_and_preserves_warning_metadata() -> None:
    adapters = synthetic_adapters()
    result = run(adapters)
    assert result.status is WorkflowStatus.VERIFIED
    assert result.artifact_status == "simulated"
    assert len(adapters.pdf_writer.calls) == 1
    assert set(adapters.pdf_writer.calls[0].protected_source_uris) == {
        "file:///synthetic/criteria.pdf",
        "file:///synthetic/verified.pdf",
    }
    assert result.pdf_result.warnings == ["Synthetic PDF writer: no file was created."]
    assert result.pdf_result.written_field_ids == ["road-rate"]
    assert result.pdf_result.page_count == 1
    assert result.output_pdf_uri is None
    assert not result.pdf_result.artifact_created


@pytest.mark.parametrize(
    "failure",
    ["defined", "unexpected", "bare_uri", "bad_uri", "wrong_uri", "bad_count", "missing_field"],
)
def test_writer_failure_retains_findings_and_never_completes(failure: str) -> None:
    adapters = synthetic_adapters()
    good = PDFWriteResult(
        output_uri="file:///synthetic/output.pdf", page_count=1, written_field_ids=["road-rate"]
    )
    writer = AsyncMock()
    if failure == "defined":
        writer.write_pdf.side_effect = PDFReadError("private file content")
    elif failure == "unexpected":
        writer.write_pdf.side_effect = RuntimeError("secret credentials")
    else:
        invalid = {
            "bare_uri": "file:///synthetic/output.pdf",
            "bad_uri": good.model_copy(update={"output_uri": "https://invalid"}),
            "wrong_uri": good.model_copy(update={"output_uri": "file:///synthetic/other.pdf"}),
            "bad_count": good.model_copy(update={"page_count": 0}),
            "missing_field": good.model_copy(update={"written_field_ids": []}),
        }
        writer.write_pdf.return_value = invalid[failure]
    result = run(replace(adapters, pdf_writer=writer))
    writer.write_pdf.assert_awaited_once()
    assert result.status is WorkflowStatus.FAILED
    assert result.review.results[0].adjustment_percent == 5.0
    assert result.verification.can_complete
    assert result.output_pdf_uri is None and result.pdf_result is None
    assert result.pdf_error is not None
    assert result.pdf_error.code == (
        PDFErrorCode.READ
        if failure == "defined"
        else PDFErrorCode.WRITE
        if failure == "unexpected"
        else PDFErrorCode.INVALID_RESULT
    )
    assert (
        "secret" not in result.model_dump_json() and "private file" not in result.model_dump_json()
    )


def test_missing_output_tools_or_map_and_conflicting_source_do_not_write() -> None:
    adapters = synthetic_adapters()
    assert run(replace(adapters, pdf_writer=None)).status is WorkflowStatus.VERIFIED
    assert run(replace(adapters, pdf_writer=None)).artifact_status == "unavailable"
    assert (
        run(
            adapters,
            synthetic_request("completed").model_copy(update={"pdf_template_uri": None}),
        ).status
        is WorkflowStatus.FAILED
    )
    assert (
        run(adapters, synthetic_request("completed").model_copy(update={"field_map": None})).status
        is WorkflowStatus.FAILED
    )
    request = synthetic_request("completed")
    request.output_pdf_uri = request.pdf_template_uri
    result = run(adapters, request)
    assert result.status is WorkflowStatus.FAILED
    assert result.pdf_error.code is PDFErrorCode.SOURCE_DESTINATION_CONFLICT
    assert not adapters.pdf_writer.calls

    request = synthetic_request("completed")
    request.output_pdf_uri = request.case_document_uri
    result = run(adapters, request)
    assert result.status is WorkflowStatus.FAILED
    assert result.pdf_error.code is PDFErrorCode.SOURCE_DESTINATION_CONFLICT
    assert not adapters.pdf_writer.calls
