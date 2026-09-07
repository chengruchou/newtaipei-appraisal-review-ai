from __future__ import annotations

import json
import runpy
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import jsonschema
import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.synthetic import synthetic_material
from appraisal_review.application.revisions import RevisionSnapshot
from appraisal_review.application.service_guards import (
    IdempotencyRecord,
    Principal,
    ServiceFault,
    admit_action,
    admit_response,
    check_idempotency,
    submission_digest,
)
from appraisal_review.domain.confidence import confirm_side
from appraisal_review.domain.review_contracts import content_digest
from appraisal_review.domain.service_contracts import (
    ActionKind,
    ActorReference,
    AllowedAction,
    Budget,
    DecisionEvent,
    DocumentReference,
    HumanResponse,
    HumanTask,
    Permission,
    PublicValue,
    ReviewSubmission,
    RunReference,
    ServiceErrorCode,
    ServiceResult,
    ToolOutcome,
    ValueRevision,
    VerificationDiagnostic,
)

ROOT = Path(__file__).resolve().parents[2]
EXPORT = runpy.run_path(str(ROOT / "scripts/export_service_contracts.py"))


@pytest.fixture
def values():
    return EXPORT["fixtures"]()


@pytest.fixture
def principal():
    return Principal(
        ActorReference(actor_id="fixture-reviewer", kind="human"),
        frozenset({"synthetic-case"}),
        frozenset(Permission),
    )


def test_export_and_examples_are_current_and_roundtrip(tmp_path):
    EXPORT["export"](tmp_path)
    schema = json.loads((ROOT / "schemas/service-v1.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    models = {m.__name__: m for m in EXPORT["MODELS"]}
    index = json.loads((ROOT / "examples/service-v1/index.json").read_text())
    for relative in [
        "schemas/service-v1.json",
        "schemas/local-service-v1.json",
        "examples/local-service-v1.json",
        "examples/service-v1/index.json",
        *[f"examples/service-v1/{name}" for name in index],
    ]:
        assert (ROOT / relative).read_bytes() == (tmp_path / relative).read_bytes()
    for name, model_name in index.items():
        data = json.loads((ROOT / "examples/service-v1" / name).read_text())
        model = models[model_name].model_validate(data)
        assert model.model_dump(mode="json") == data
        jsonschema.validate(data, {**schema, "$ref": f"#/$defs/{model_name}"})
        assert "storage_uri" not in json.dumps(data)
        assert "file:///" not in json.dumps(data)


@pytest.mark.parametrize(
    "extra",
    [
        {"storage_uri": "file:///private"},
        {"verified": True},
        {"actor": "fixture-reviewer"},
        {"schema_version": "service-v2"},
    ],
)
def test_external_document_rejects_authority_and_uris(values, extra):
    data = values["revision"].documents[0].model_dump(mode="json")
    with pytest.raises(ValidationError):
        DocumentReference.model_validate({**data, **extra})


@pytest.mark.parametrize(
    "change",
    [
        {"message": "Cannot open file:///private/case.pdf"},
        {"code": "file:///private/case.pdf"},
        {"message": "A current source registry is required to review the material."},
        {"source_uri": "file:///private/case.pdf"},
    ],
)
def test_verification_diagnostic_rejects_private_or_mismatched_reasons(change):
    data = {
        "code": "source_binding",
        "message": "Requested documents must match the configured review sources.",
    }
    with pytest.raises(ValidationError):
        VerificationDiagnostic.model_validate({**data, **change})


def test_decimal_blank_null_and_finite():
    value = PublicValue(state="present", value=Decimal("0.0100"), raw_text="0.0100", confidence=0)
    assert value.model_dump(mode="json")["value"] == "0.0100"
    assert content_digest(value) == content_digest(
        PublicValue.model_validate_json(value.model_dump_json())
    )
    blank = PublicValue(state="blank", raw_text="", confidence=None)
    assert blank.model_dump(mode="json")["value"] is None
    assert blank.model_dump(mode="json")["confidence"] is None
    for invalid in (Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValidationError):
            PublicValue(state="present", value=invalid, raw_text="invalid")
    with pytest.raises(ValidationError):
        PublicValue(state="blank", value="0", raw_text="")
    with pytest.raises(ValidationError):
        PublicValue(state="present", value="1", raw_text="1", confidence=float("nan"))


def test_snapshot_is_detached_revision_invalidates_confirmation():
    material = synthetic_material()
    pair = material.facts.pairs[0]
    pair.pair.target.confidence = 0
    confirm_side(pair, "target", reviewer="fixture-reviewer")
    old = RevisionSnapshot.capture(material, "r1")
    original_digest = old.revision.reference.material_digest
    metadata = old.revision
    metadata.rules[0].context.target_id = "changed-external-context"
    assert old.revision.rules[0].context.target_id != "changed-external-context"
    material.facts.observed[0].value = "99"
    detached = old.material
    detached.facts.observed[0].value = "88"
    assert old.material.facts.observed[0].value == "5"
    child = old.revise(old.material, "r2")
    assert child.revision.parent == old.revision.reference
    assert child.revision.reference.material_digest != original_digest
    assert child.material.policy.identity.version == child.material.facts.identity.version == "r2"
    assert child.material.facts.pairs[0].target_reliability.confirmation is None
    assert child.material.facts.pairs[0].target_reliability.method == "model_proposed"
    assert child.material.facts.pairs[0].pair.target.confidence == 0
    assert old.material.facts.pairs[0].target_reliability.confirmation is not None
    with pytest.raises(ValueError):
        old.revise(old.material, "r1")
    different_case = old.material
    different_case.facts.identity.case_id = "other"
    with pytest.raises(ValueError):
        old.revise(different_case, "r2")


def test_correction_cannot_inherit_native_authority():
    material = synthetic_material()
    old = RevisionSnapshot.capture(material, "r1")
    material.facts.pairs[0].pair.target.value.value = 99.0
    child = old.revise(material, "r2")
    assert child.material.facts.pairs[0].target_reliability.method == "model_proposed"
    assert child.material.facts.pairs[0].comparable_reliability.method == "native_numeric"
    assert child.material.facts.pairs[0].pair.target.confidence == 0.99


def test_canonical_material_uses_keys_not_insertion_order():
    material = synthetic_material()
    payload = material.model_dump(mode="json")
    reordered = type(material).model_validate(dict(reversed(list(payload.items()))))
    assert content_digest(material) == content_digest(reordered)
    old = content_digest(material)
    material.facts.pairs[0].pair.target.confidence = 0
    assert content_digest(material) != old


def test_response_acceptance_is_admission_only(values, principal):
    task, command = values["task"], values["response"]
    accepted = admit_response(task, command, principal, current=task.run.revision)
    assert accepted.actor == principal.actor
    assert accepted.command == command
    assert task.state == "open" and task.version == 1
    assert not hasattr(accepted, "approved")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"expected_version": 2}, ServiceErrorCode.CONFLICT),
        ({"task_id": UUID(int=99)}, ServiceErrorCode.NOT_FOUND),
        ({"side_digest": "f" * 64}, ServiceErrorCode.CONFLICT),
        ({"action": "approve"}, ServiceErrorCode.UNAUTHORIZED),
    ],
)
def test_response_stale_or_wrong_action(values, principal, change, code):
    task = values["task"]
    command = HumanResponse.model_validate({**values["response"].model_dump(), **change})
    with pytest.raises(ServiceFault) as error:
        admit_response(task, command, principal, current=task.run.revision)
    assert error.value.problem.code == code
    assert "file:" not in error.value.problem.model_dump_json()


@pytest.mark.parametrize(
    "variation", ["no_case", "no_permission", "model", "stale_revision", "closed_task"]
)
def test_response_checks_trusted_principal_and_current_revision(values, principal, variation):
    task, response = values["task"], values["response"]
    current = task.run.revision
    if variation == "no_case":
        principal = Principal(principal.actor, frozenset(), principal.permissions)
    if variation == "no_permission":
        principal = Principal(principal.actor, principal.case_ids, frozenset({Permission.REVIEW}))
    if variation == "model":
        principal = Principal(
            ActorReference(actor_id="fixture-policy", kind="model"),
            principal.case_ids,
            principal.permissions,
        )
    if variation == "stale_revision":
        current = current.model_copy(update={"revision_id": "r2", "material_digest": "f" * 64})
    if variation == "closed_task":
        task = task.model_copy(update={"state": "answered"})
    with pytest.raises(ServiceFault):
        admit_response(task, response, principal, current=current)


@pytest.mark.parametrize(
    "extra",
    [{"actor": {"actor_id": "admin", "kind": "human"}}, {"roles": ["admin"]}, {"verified": True}],
)
def test_response_cannot_choose_identity(values, extra):
    with pytest.raises(ValidationError):
        HumanResponse.model_validate({**values["response"].model_dump(), **extra})


def test_corrections_have_separate_original_proposed_and_server_record(values):
    original = PublicValue(state="blank", raw_text="", confidence=0)
    proposed = PublicValue(state="present", value="5", raw_text="proposal", confidence=0)
    correction = ValueRevision(subject_id="rate", original=original, proposed=proposed)
    response = HumanResponse.model_validate(
        {**values["response"].model_dump(), "action": "correct", "correction": correction}
    )
    assert response.correction.original.state == "blank"
    trusted = ValueRevision(
        subject_id="rate",
        original=original,
        proposed=proposed,
        corrected=proposed,
        corrected_by=ActorReference(actor_id="fixture-reviewer", kind="human"),
    )
    with pytest.raises(ValidationError):
        HumanResponse.model_validate({**response.model_dump(), "correction": trusted})
    with pytest.raises(ValidationError):
        HumanTask.model_validate(
            {**values["task"].model_dump(), "required_permission": "publish_artifact"}
        )


def test_idempotency_is_canonical_principal_scoped_and_conflicts(values, principal):
    request = values["submission"]
    record = IdempotencyRecord(
        principal.actor.actor_id, request.idempotency_key, submission_digest(request), UUID(int=42)
    )
    assert check_idempotency(record, principal, request) == UUID(int=42)
    reordered = request.model_copy(update={"documents": tuple(reversed(request.documents))})
    assert submission_digest(request) == submission_digest(reordered)
    assert submission_digest(request) == submission_digest(
        request.model_copy(update={"idempotency_key": "new"})
    )
    changed = request.model_copy(
        update={"revision": request.revision.model_copy(update={"revision_id": "r2"})}
    )
    with pytest.raises(ServiceFault, match="version_conflict"):
        check_idempotency(record, principal, changed)
    with pytest.raises(ServiceFault, match="unauthorized"):
        check_idempotency(
            record, principal, request.model_copy(update={"idempotency_key": "other"})
        )
    other = Principal(
        ActorReference(actor_id="other", kind="human"), principal.case_ids, principal.permissions
    )
    with pytest.raises(ServiceFault, match="unauthorized"):
        check_idempotency(record, other, request)
    with pytest.raises(ValidationError):
        ReviewSubmission.model_validate(
            {**request.model_dump(), "documents": [request.documents[0]] * 2}
        )


def test_model_actions_are_constrained_and_events_require_execution(values):
    proposal = values["proposal"]
    kwargs = dict(
        proposer=values["proposal"].proposer,
        run=proposal.run,
        revision=values["revision"],
        allowed=(AllowedAction(action=ActionKind.HUMAN, prerequisites=("blocked",)),),
        satisfied=frozenset({"blocked"}),
        budget=Budget(steps_remaining=1, model_calls_remaining=1, retries_remaining=0),
    )
    assert admit_action(proposal, **kwargs) is None
    for overrides in (
        {"allowed": ()},
        {"satisfied": frozenset()},
        {"budget": Budget(steps_remaining=0, model_calls_remaining=1, retries_remaining=0)},
        {"budget": Budget(steps_remaining=1, model_calls_remaining=0, retries_remaining=0)},
        {"run": proposal.run.model_copy(update={"run_id": UUID(int=100)})},
    ):
        with pytest.raises(ServiceFault):
            admit_action(proposal, **{**kwargs, **overrides})
    with pytest.raises(ValidationError):
        type(proposal).model_validate({**proposal.model_dump(), "action": "approve"})
    event = values["decision-rejected"]
    with pytest.raises(ValidationError):
        DecisionEvent.model_validate({**event.model_dump(), "executed_action": proposal.action})
    with pytest.raises(ValidationError):
        DecisionEvent.model_validate({**event.model_dump(), "disposition": "executed"})
    tool = ToolOutcome(outcome="succeeded", result_digest="a" * 64)
    executed = DecisionEvent.model_validate(
        {
            **event.model_dump(),
            "disposition": "executed",
            "executor": ActorReference(actor_id="fixture-executor", kind="system"),
            "executed_action": proposal.action,
            "tool_result": tool,
        }
    )
    assert executed.tool_result == tool
    with pytest.raises(ValidationError):
        DecisionEvent.model_validate({**executed.model_dump(), "disposition": "failed"})


def test_source_actions_reject_wrong_identity_purpose_and_page(values):
    proposal = values["proposal"].model_copy(update={"action": ActionKind.EXTRACT})
    kwargs = dict(
        proposer=values["proposal"].proposer,
        run=proposal.run,
        revision=values["revision"],
        allowed=(
            AllowedAction(action=ActionKind.EXTRACT),
            AllowedAction(action=ActionKind.REFERENCE),
        ),
        satisfied=frozenset(),
        budget=Budget(steps_remaining=2, model_calls_remaining=1, retries_remaining=0),
    )
    with pytest.raises(ServiceFault):
        admit_action(proposal, **kwargs)
    proposal = proposal.model_copy(update={"document": values["revision"].documents[0], "page": 1})
    admit_action(proposal, **kwargs)
    with pytest.raises(ServiceFault):
        admit_action(proposal.model_copy(update={"action": ActionKind.REFERENCE}), **kwargs)
    with pytest.raises(ServiceFault):
        admit_action(
            proposal.model_copy(
                update={"document": proposal.document.model_copy(update={"version": "old"})}
            ),
            **kwargs,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"business_status": "completed"},
        {"artifact_status": "written"},
        {"execution_status": "failed"},
        {"execution_status": "queued"},
        {"execution_status": "succeeded", "business_status": None},
    ],
)
def test_result_cannot_claim_completion_or_hide_failure(values, overrides):
    with pytest.raises(ValidationError):
        ServiceResult.model_validate({**values["result-needs-review"].model_dump(), **overrides})


def test_session_is_not_a_run(values):
    run = values["task"].run
    with pytest.raises(ValidationError):
        RunReference.model_validate({**run.model_dump(), "runtime_session_id": "session"})
    result = RunReference.model_validate(
        {**run.model_dump(), "runtime_session_id": "session", "attempt_id": UUID(int=12)}
    )
    assert result.run_id != result.attempt_id


def test_local_configuration_example_matches_schema():
    from appraisal_review.adapters.local.service import LocalServiceConfiguration

    data = json.loads((ROOT / "examples/local-service-v1.json").read_text())
    schema = json.loads((ROOT / "schemas/local-service-v1.json").read_text())
    model = LocalServiceConfiguration.model_validate_json(json.dumps(data))
    assert model.writer is None
    assert model.expected_material_digest == "0" * 64
    jsonschema.validate(model.model_dump(mode="json"), schema)


def test_publication_response_binds_result_and_permission(values, principal):
    task = HumanTask.model_validate(
        {
            **values["task"].model_dump(),
            "kind": "publication_authorization",
            "required_permission": "publish_artifact",
            "side": None,
            "result_digest": "b" * 64,
            "allowed_responses": ["authorize_publication"],
        }
    )
    command = HumanResponse.model_validate(
        {
            **values["response"].model_dump(),
            "action": "authorize_publication",
            "side_digest": None,
            "result_digest": "b" * 64,
        }
    )
    assert admit_response(task, command, principal, current=task.run.revision).command == command
    with pytest.raises(ServiceFault, match="version_conflict"):
        admit_response(
            task,
            command.model_copy(update={"result_digest": "c" * 64}),
            principal,
            current=task.run.revision,
        )
    with pytest.raises(ValidationError):
        HumanTask.model_validate({**task.model_dump(), "result_digest": None})


def test_action_cannot_claim_system_identity_to_bypass_model_budget(values):
    proposal = values["proposal"]
    forged = proposal.model_copy(
        update={"proposer": ActorReference(actor_id="fixture-policy", kind="system")}
    )
    kwargs = dict(
        proposer=values["proposal"].proposer,
        run=proposal.run,
        revision=values["revision"],
        allowed=(AllowedAction(action=ActionKind.HUMAN),),
        satisfied=frozenset(),
        budget=Budget(steps_remaining=1, model_calls_remaining=0, retries_remaining=0),
    )
    with pytest.raises(ServiceFault, match="unauthorized"):
        admit_action(forged, **kwargs)
    with pytest.raises(ServiceFault, match="capability_unavailable"):
        admit_action(proposal, **kwargs)
    # A real trusted system origin can still run without spending a model call.
    admit_action(forged, **{**kwargs, "proposer": forged.proposer})
