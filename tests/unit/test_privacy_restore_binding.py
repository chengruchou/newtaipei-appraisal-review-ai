"""Exact successful export binding excludes unrelated failed local mapping reads."""

from unittest.mock import Mock

import pytest
from test_privacy_bridge import bridge as bridge
from test_privacy_document_sink import exact_preview
from test_privacy_restore_resolver import published

from appraisal_review.domain.privacy_mapping import MappingFault
from appraisal_review.domain.privacy_models import public_manifest_json


def test_valid_export_survives_unrelated_failed_export_with_unreadable_mapping(bridge):
    coordinator, state, _, _ = published(bridge)
    first_handles = set(bridge.owner._maps)
    # A second real export persists a distinct map before its sink fails. Its
    # unavailable local evidence must not participate in selecting export A.
    bridge.owner.sink = Mock()
    bridge.owner.sink.accept.side_effect = RuntimeError("Synthetic transfer failed")
    base, _, _ = exact_preview(bridge)
    assert bridge.client.post(base + "/transfer", json={}).status_code == 409
    failed_ids = set(bridge.owner._maps) - first_handles
    assert len(failed_ids) == 1
    failed_id = failed_ids.pop()
    failed_handle = bridge.owner._maps[failed_id]
    path = bridge.path / "maps" / f"{failed_handle.case_id.hex}-{failed_id.hex}.map"
    path.write_bytes(b"corrupted synthetic ciphertext")
    original_read = bridge.owner.mappings.read
    with pytest.raises(MappingFault):
        original_read(failed_handle)
    reads = []

    def read(handle):
        reads.append(handle.map_id)
        return original_read(handle)

    bridge.owner.mappings.read = read
    result = coordinator.resolve(bridge.owner.principal_id, state.id)
    assert result.plan.map_id in first_handles
    assert result.authority.permits(result.plan)
    assert failed_id in bridge.owner._maps
    assert failed_id not in bridge.owner._exported_maps.values()
    assert failed_id not in reads
    assert result.plan.map_id in reads


def test_exact_selected_mapping_corruption_is_not_skipped(bridge):
    coordinator, state, _, _ = published(bridge)
    handle = next(iter(bridge.owner._maps.values()))
    path = bridge.path / "maps" / f"{handle.case_id.hex}-{handle.map_id.hex}.map"
    path.write_bytes(b"corrupted selected synthetic ciphertext")
    with pytest.raises(MappingFault):
        coordinator.resolve(bridge.owner.principal_id, state.id)
    assert not list(bridge.path.glob("authorized-download-*"))


@pytest.mark.parametrize("fault", ["absent", "wrong-map", "record-mismatch"])
def test_exact_manifest_index_and_selected_record_are_both_required(bridge, fault):
    coordinator, state, _, _ = published(bridge)
    manifest = state.value.forms.attestation.claims.manifest
    key = public_manifest_json(manifest)
    selected = bridge.owner._exported_maps[key]
    base, _, _ = exact_preview(bridge)
    assert bridge.client.post(base + "/transfer", json={}).status_code == 200
    other = next(i for i in bridge.owner._maps if i != selected)
    if fault == "absent":
        bridge.owner._exported_maps.pop(key)
    elif fault == "wrong-map":
        bridge.owner._exported_maps[key] = other
    else:
        wrong_record = bridge.owner.mappings.read(bridge.owner._maps[other])
        bridge.owner.mappings.read = Mock(return_value=wrong_record)
    with pytest.raises(ValueError):
        coordinator.resolve(bridge.owner.principal_id, state.id)
    assert not list(bridge.path.glob("authorized-download-*"))


def test_session_admission_denial_reaches_gate_before_mapping_or_transfer(bridge):
    from test_privacy_bridge import prepared

    preview, _ = prepared(bridge)
    admission = Mock()
    admission.check.side_effect = ValueError("Synthetic unapproved competition data")
    bridge.owner.competition_admission = admission
    response = bridge.client.post(f"/exports/{preview['preview_id']}/transfer", json={})
    assert response.status_code == 409
    admission.check.assert_called_once()
    bridge.sink.accept.assert_not_called()
    assert bridge.owner._maps == {}
    assert bridge.owner._exported_maps == {}
    assert not list((bridge.path / "maps").glob("*.map"))
