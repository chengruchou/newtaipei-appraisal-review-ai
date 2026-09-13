"""The assembled-envelope admission admits exactly the declared send, nothing else."""

import json

import pytest

from appraisal_review.adapters.aws.assembled_admission import (
    OutboundModelIntent,
    TrustedAssemblyAdmission,
    declare_outbound_envelope,
)
from appraisal_review.domain.competition_data import CompetitionDataFault, DataPart

MODEL = "us.anthropic.claude-sonnet-4-6"
SYSTEM = "You are the review selector."
PAYLOAD = json.dumps({"snapshot": {"case": "synthetic"}}, separators=(",", ":"))


def intent(provenance: str = "synthetic_fixture") -> OutboundModelIntent:
    return OutboundModelIntent(
        model_id=MODEL, system_text=SYSTEM, user_payload=PAYLOAD, provenance=provenance
    )


def parts(
    body: dict | None = None, url: str | None = None, raw_body: bytes | None = None
) -> tuple[DataPart, ...]:
    content = (
        raw_body
        if raw_body is not None
        else json.dumps(
            body
            if body is not None
            else {
                "system": [{"text": SYSTEM}],
                "messages": [{"role": "user", "content": [{"text": PAYLOAD}]}],
                "inferenceConfig": {"maxTokens": 1200, "temperature": 0},
            }
        ).encode()
    )
    return (
        DataPart("bedrock.request.body", "model_prompt", content),
        DataPart(
            "bedrock.request.url",
            "metadata",
            (url or f"https://bedrock-runtime.us-west-2.amazonaws.com/model/{MODEL}/converse")
            .encode(),
        ),
    )


ADMISSION = TrustedAssemblyAdmission(frozenset({"synthetic_fixture"}))


class TestTrustedAssemblyAdmission:
    def test_declared_matching_envelope_passes(self) -> None:
        with declare_outbound_envelope(intent()):
            ADMISSION.check(parts())

    def test_undeclared_send_refused(self) -> None:
        with pytest.raises(CompetitionDataFault):
            ADMISSION.check(parts())

    def test_unlisted_provenance_refused(self) -> None:
        with declare_outbound_envelope(intent(provenance="official_batch")):
            with pytest.raises(CompetitionDataFault):
                ADMISSION.check(parts())

    def test_payload_mismatch_refused(self) -> None:
        drifted = {
            "system": [{"text": SYSTEM}],
            "messages": [{"role": "user", "content": [{"text": PAYLOAD + "x"}]}],
            "inferenceConfig": {"maxTokens": 1200, "temperature": 0},
        }
        with declare_outbound_envelope(intent()), pytest.raises(CompetitionDataFault):
            ADMISSION.check(parts(body=drifted))

    def test_extra_message_refused(self) -> None:
        drifted = {
            "system": [{"text": SYSTEM}],
            "messages": [
                {"role": "user", "content": [{"text": PAYLOAD}]},
                {"role": "user", "content": [{"text": "smuggled"}]},
            ],
        }
        with declare_outbound_envelope(intent()), pytest.raises(CompetitionDataFault):
            ADMISSION.check(parts(body=drifted))

    def test_unmodeled_top_level_field_refused(self) -> None:
        drifted = {
            "system": [{"text": SYSTEM}],
            "messages": [{"role": "user", "content": [{"text": PAYLOAD}]}],
            "toolConfig": {"tools": []},
        }
        with declare_outbound_envelope(intent()), pytest.raises(CompetitionDataFault):
            ADMISSION.check(parts(body=drifted))

    def test_wrong_model_url_refused(self) -> None:
        with declare_outbound_envelope(intent()), pytest.raises(CompetitionDataFault):
            ADMISSION.check(
                parts(
                    url="https://bedrock-runtime.us-west-2.amazonaws.com/model/other/converse"
                )
            )

    def test_url_encoded_model_id_accepted(self) -> None:
        encoded = MODEL.replace(":", "%3A")
        with declare_outbound_envelope(intent()):
            ADMISSION.check(
                parts(
                    url="https://bedrock-runtime.us-west-2.amazonaws.com/model/"
                    + encoded
                    + "/converse"
                )
            )

    def test_non_json_body_refused(self) -> None:
        with declare_outbound_envelope(intent()), pytest.raises(CompetitionDataFault):
            ADMISSION.check(parts(raw_body=b"\xff\xfe not json"))

    def test_declaration_scoped_to_context(self) -> None:
        with declare_outbound_envelope(intent()):
            ADMISSION.check(parts())
        with pytest.raises(CompetitionDataFault):
            ADMISSION.check(parts())
