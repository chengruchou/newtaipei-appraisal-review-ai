"""Export deterministic extraction/evaluation schemas and synthetic consumer examples."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from appraisal_review.domain.evaluation_contracts import (
    EvaluationInput,
    EvaluationManifest,
    ScoringPolicy,
)
from appraisal_review.domain.extraction_contracts import (
    AttemptTelemetry,
    ExecutionBudget,
    ExtractionContext,
    HandoffLocation,
    HandoffRequest,
    PageOutcome,
    PageRequest,
    ProviderConfiguration,
    ProviderTelemetry,
    SanitizedSourceReference,
)
from appraisal_review.domain.extraction_models import PageProposal
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    RevisionReference,
    RunReference,
)

SYNTHETIC_BYTES = b"Synthetic snapshot contract test bytes; not a PDF or privacy certificate."


def fixtures() -> dict[str, BaseModel]:
    source = SanitizedSourceReference(
        document=DocumentReference(
            case_id="synthetic-case",
            document_id="synthetic-forms",
            version="v1",
            content_hash=hashlib.sha256(SYNTHETIC_BYTES).hexdigest(),
            purpose="forms",
        ),
        privacy_contract_version="privacy-v1",
        privacy_manifest_digest="a" * 64,
        page_count=2,
    )
    request = PageRequest(
        run=RunReference(
            run_id=UUID(int=1),
            revision=RevisionReference(
                case_id="synthetic-case",
                revision_id="r1",
                material_digest="b" * 64,
            ),
        ),
        source=source,
        page=1,
        context=ExtractionContext(language="zh-Hant", task="propose_case"),
    )
    configuration = ProviderConfiguration(
        provider="synthetic",
        model_id="synthetic-model",
        region="synthetic-region",
        api="synthetic-api",
        routing_regions=("synthetic-region",),
        temperature=0,
        max_output_tokens=1000,
    )
    telemetry = ProviderTelemetry(
        configuration=configuration,
        prompt_version="synthetic-prompt-v1",
        prompt_digest="c" * 64,
        elapsed_seconds=0.5,
        attempts=(
            AttemptTelemetry(
                attempt=1,
                completion="returned",
                failure=None,
                input_tokens=None,
                output_tokens=None,
                elapsed_seconds=0.5,
                backoff_seconds=0,
            ),
        ),
    )
    handoff = HandoffRequest(
        request=request,
        reason="page_failed",
        locations=(HandoffLocation(page=1),),
    )
    budget = ExecutionBudget(
        max_pages=2,
        max_calls=4,
        max_attempts_per_page=2,
        max_concurrency=1,
        max_input_bytes=100000,
        max_context_characters=5000,
        max_output_tokens=4000,
        max_elapsed_seconds=60,
    )
    return {
        "request.json": request,
        "candidate.json": PageOutcome(
            request=request,
            status="candidate",
            proposal=PageProposal(),
            failure=None,
            telemetry=telemetry,
            handoffs=(),
        ),
        "failed-page.json": PageOutcome(
            request=request,
            status="failed",
            proposal=None,
            failure="privacy_unavailable",
            telemetry=ProviderTelemetry(
                configuration=configuration,
                prompt_version="synthetic-prompt-v1",
                prompt_digest="c" * 64,
                attempts=(),
                elapsed_seconds=0,
            ),
            handoffs=(handoff,),
        ),
        "handoff.json": handoff,
        "evaluation.json": EvaluationManifest(
            dataset_id="synthetic-contracts",
            dataset_version="v1",
            dataset_digest="d" * 64,
            dataset_kind="synthetic",
            execution_kind="mocked",
            split="development",
            split_digest="e" * 64,
            golden_digest="f" * 64,
            golden_schema_version="synthetic-v1",
            adjudication="synthetic_expectations",
            inputs=(EvaluationInput(source=source, pages=(2, 1)),),
            pipeline_version="synthetic-v1",
            prompt_version="synthetic-prompt-v1",
            prompt_digest="c" * 64,
            proposal_schema_digest="1" * 64,
            rendering_configuration_digest="2" * 64,
            configuration=configuration,
            budget=budget,
            scoring=ScoringPolicy(
                policy_version="synthetic-v1",
                policy_digest="3" * 64,
                numeric_absolute_tolerance="0",
                numeric_relative_tolerance="0",
                unit_policy="exact",
                unit_policy_digest=None,
                localization="not_measured",
                minimum_iou=None,
                acceptance_thresholds_digest=None,
            ),
            repeats=1,
            timing_scope="provider_only",
            cost_policy=None,
        ),
    }


def artifacts() -> dict[str, object]:
    examples = fixtures()
    models = (PageRequest, PageOutcome, HandoffRequest)
    _, extraction_schema = models_json_schema([(m, "serialization") for m in models])
    _, evaluation_schema = models_json_schema([(EvaluationManifest, "serialization")])
    for schema in (extraction_schema, evaluation_schema):
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    result: dict[str, object] = {
        "schemas/extraction-v1.json": extraction_schema,
        "schemas/evaluation-v1.json": evaluation_schema,
    }
    index = {}
    for name, model in examples.items():
        result[f"examples/extraction-v1/{name}"] = model.model_dump(mode="json")
        bundle = "evaluation-v1" if isinstance(model, EvaluationManifest) else "extraction-v1"
        index[name] = {"schema": f"../../schemas/{bundle}.json", "model": type(model).__name__}
    result["examples/extraction-v1/index.json"] = index
    return result


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for name, value in artifacts().items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
