"""Private observations cannot become diagnostic content or cross request boundaries."""

import asyncio
import json
from uuid import uuid4

import pytest

from appraisal_review.application.privacy_diagnostics import (
    RestoreDiagnostics,
    diagnostic_request,
    note_failure,
    note_ocr_failure,
    restore_stage,
)
from appraisal_review.domain.privacy_diagnostics import (
    LocalRestoreFailure,
    RestoreFailureCode,
    RestoreStage,
)
from appraisal_review.domain.privacy_mapping import MappingError, MappingFault

CANARY = "Synthetic private exception, source path and observation canary"


def test_parallel_requests_keep_first_swallowed_failure_and_bounded_vocabulary():
    async def run():
        entered, released = asyncio.Event(), asyncio.Event()
        left = RestoreDiagnostics(str(uuid4()), "download")
        right = RestoreDiagnostics(str(uuid4()), "restore")

        async def denied():
            with diagnostic_request(left), restore_stage(RestoreStage.PUBLICATION):
                try:
                    with restore_stage(RestoreStage.MAPPING):
                        raise MappingFault(MappingError.EXPIRED)
                except MappingFault:
                    pass
                entered.set()
                await released.wait()
                note_failure(ValueError(CANARY))
            return left.record(409)

        async def allowed():
            await entered.wait()
            with diagnostic_request(right), restore_stage(RestoreStage.SOURCE):
                await asyncio.to_thread(lambda: None)
            released.set()
            return right.record(200)

        return await asyncio.gather(denied(), allowed())

    denied, allowed = asyncio.run(run())
    assert denied["failure"] == {"stage": "mapping", "code": "mapping_expired"}
    assert allowed["failure"] is None and allowed["code"] == "ok"
    assert denied["request_id"] != allowed["request_id"]
    assert CANARY not in json.dumps([denied, allowed])
    assert {item["stage"] for item in allowed["timings"]} == {"source"}


@pytest.mark.parametrize("fault", ["mapping", "local", "attribute"])
def test_malformed_adapter_fault_cannot_inject_code_or_replace_original_error(fault):
    if fault == "mapping":
        error = MappingFault(MappingError.LOCKED)
        error.code = CANARY
    elif fault == "local":
        error = LocalRestoreFailure(RestoreFailureCode.AUTHORITY_DENIED)
        error.diagnostic_code = CANARY
    else:

        class BrokenFault(MappingFault):
            def __getattribute__(self, name):
                if name == "code":
                    raise ValueError(CANARY)
                return super().__getattribute__(name)

        error = BrokenFault(MappingError.LOCKED)
    value = RestoreDiagnostics(str(uuid4()), "download")
    with (
        diagnostic_request(value),
        pytest.raises(type(error)) as raised,
        restore_stage(RestoreStage.MAPPING),
    ):
        raise error
    assert raised.value is error
    record = value.record(409)
    assert record["failure"]["code"] in ("validation_failed", "operation_failed")
    assert CANARY not in json.dumps(record)


def test_ocr_diagnostics_retain_counts_without_copying_observations():
    value = RestoreDiagnostics(str(uuid4()), "restore")
    evidence = {
        "stage": "published",
        "reason": "uncertain_ocr",
        "page": 2,
        "observation_count": 99,
        "low_confidence_count": 7,
        "observations": [{"text": CANARY, "confidence": 0.0}],
        "engine_path": CANARY,
    }
    unchanged = json.dumps(evidence)
    with diagnostic_request(value):
        note_ocr_failure(evidence)
    record = value.record(409)
    assert record["observation_count"] == 99 and record["low_confidence_count"] == 7
    assert record["code"] == "privacy_verification_failed"
    assert record["stage"] == "published" and record["reason"] == "uncertain_ocr"
    assert CANARY not in json.dumps(record)
    assert json.dumps(evidence) == unchanged


def test_many_phase_probes_remain_bounded_and_do_not_capture_arguments():
    value = RestoreDiagnostics(str(uuid4()), "download")
    with diagnostic_request(value):
        for _ in range(65540):
            with restore_stage(RestoreStage.PUBLICATION):
                pass
        note_ocr_failure({"stage": CANARY, "reason": CANARY, "page": 10**100})
    record = value.record(200)
    assert len(record["timings"]) == 1
    assert record["timings"][0]["calls"] == 65535
    assert record["timings"][0]["failed_calls"] == 0
    assert len(json.dumps(record)) < 8192 and CANARY not in json.dumps(record)
    assert record["failure"] is None
