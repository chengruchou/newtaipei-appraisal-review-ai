"""Competition denial precedes real local PDF transfer, even after privacy approval."""

from unittest.mock import Mock

import pytest
from test_privacy_bridge import bridge as bridge
from test_privacy_bundle import approval
from test_privacy_document_sink import documents_for, exact_preview, sink_for
from test_privacy_export import gate_fixture

from appraisal_review.adapters.local.privacy.detector import detect_candidates
from appraisal_review.adapters.local.privacy_document_sink import CloudExportSink
from appraisal_review.application.privacy_guards import PrivacyFault
from appraisal_review.domain.competition_data import CompetitionDataFault
from appraisal_review.domain.document_transfer import DocumentFault
from appraisal_review.domain.privacy_models import PrivacyRegion, SensitiveCategory
from appraisal_review.domain.privacy_scan import TextObservation


def test_privacy_confirmation_cannot_override_competition_denial(tmp_path):
    gate, command, _, confirmation, sink, _ = gate_fixture(tmp_path)
    admission = Mock()
    admission.check.side_effect = CompetitionDataFault("competition_data_prohibited")
    gate._competition_admission = admission
    with pytest.raises(PrivacyFault, match="privacy_verification_failed"):
        gate.export(command, approval(command))
    confirmation.confirm.assert_not_called()
    sink.accept.assert_not_called()
    assert gate.mapping_handle is None


def test_competition_revocation_during_confirmation_prevents_transport(tmp_path):
    gate, command, _, confirmation, sink, _ = gate_fixture(tmp_path)
    admission = Mock()
    admission.check.side_effect = [None, CompetitionDataFault("competition_data_unreviewed")]
    gate._competition_admission = admission
    with pytest.raises(PrivacyFault, match="privacy_verification_failed"):
        gate.export(command, approval(command))
    confirmation.confirm.assert_called_once()
    sink.accept.assert_not_called()
    assert admission.check.call_count == 2
    assert gate.mapping_handle is not None


def test_default_cloud_sink_never_treats_local_privacy_approval_as_cloud_permission(bridge):
    service, principal, key, key_id = documents_for(bridge)
    sink = CloudExportSink(
        service,
        principal,
        key,
        key_id,
        {(bridge.source.case_id, bridge.source.document_id): "forms"},
        on_admitted=Mock(),
    )
    bridge.owner.sink = sink
    base, _, _ = exact_preview(bridge)
    response = bridge.client.post(base + "/transfer", json={})
    assert response.status_code == 409
    assert response.json() == {"code": "local_privacy_request_rejected"}
    service.ingest.assert_not_called()
    assert not sink.receipts


def test_network_storage_cannot_use_local_rehearsal_escape_hatch(bridge):
    service, principal, key, key_id = documents_for(bridge)
    service.storage = Mock()
    with pytest.raises(DocumentFault):
        CloudExportSink(
            service,
            principal,
            key,
            key_id,
            {(bridge.source.case_id, bridge.source.document_id): "forms"},
            on_admitted=Mock(),
            local_rehearsal=True,
        )
    service.ingest.assert_not_called()


@pytest.mark.parametrize("changed", ["storage", "audit"])
def test_local_rehearsal_rechecks_all_side_effecting_storage_before_transfer(bridge, changed):
    service, _, _, _ = sink_for(bridge)
    base, _, _ = exact_preview(bridge)
    replacement = Mock()
    setattr(service, changed, replacement)
    response = bridge.client.post(base + "/transfer", json={})
    assert response.status_code == 409
    service.ingest.assert_not_called()
    assert not replacement.mock_calls


def test_local_rehearsal_rejects_separate_network_audit_at_construction(bridge):
    service, principal, key, key_id = documents_for(bridge)
    service.audit = Mock()
    with pytest.raises(DocumentFault):
        CloudExportSink(
            service,
            principal,
            key,
            key_id,
            {(bridge.source.case_id, bridge.source.document_id): "forms"},
            on_admitted=Mock(),
            local_rehearsal=True,
        )
    service.ingest.assert_not_called()


@pytest.mark.parametrize(
    "text", ["NT$ 1,250", "USD 200", "金額\uff1a100元", "單價 42", "Price: 100", "餘額\uff1a20"]
)
def test_monetary_candidates_do_not_require_ownership_label(text):
    observed = TextObservation(
        text=text,
        region=PrivacyRegion(page=1, bbox=(1, 2, 100, 30)),
        origin="ocr",
        confidence=0.41,
    )
    candidates = detect_candidates((observed,))
    assert any(c.category == SensitiveCategory.OWNERSHIP_FINANCIAL for c in candidates)
    assert all(c.confidence == 0.41 for c in candidates)
    assert observed.text == text and observed.confidence == 0.41
