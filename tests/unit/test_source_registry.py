"""Source registry regressions: the shipped file, validation, status discipline."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from appraisal_review.application.service_guards import ServiceFault
from appraisal_review.application.source_registry import (
    SourceEntry,
    SourceRegistry,
    get_entry,
    load_registry,
    registry_json,
    update_status,
)
from appraisal_review.domain.service_contracts import ServiceErrorCode

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "configs" / "sources" / "registry-v1.json"


def entry_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_id": "ntpc-example",
        "title": "Example dataset",
        "publisher": "Example portal",
        "portal_url": "https://data.ntpc.gov.tw/datasets/x",
        "status": "listed",
        "status_reason": "Listed for a unit test.",
        "checked_at": "2026-09-12T22:30:00+08:00",
    }
    payload.update(overrides)
    return payload


def registry_with(*entries: dict[str, object]) -> SourceRegistry:
    return SourceRegistry.model_validate({"registry_id": "registry-test", "entries": list(entries)})


def test_shipped_registry_loads_with_all_eighteen_official_sources() -> None:
    registry = load_registry(REGISTRY_PATH)

    assert len(registry.entries) == 18
    assert len({entry.source_id for entry in registry.entries}) == 18
    for entry in registry.entries:
        assert entry.portal_url.startswith("https://")
        assert entry.status_reason
        assert entry.checked_at


def test_shipped_registry_marks_only_the_two_implemented_sources_verified() -> None:
    registry = load_registry(REGISTRY_PATH)

    verified = {e.source_id: e for e in registry.entries if e.status != "listed"}
    assert set(verified) == {"ntpc-parks", "ntpc-bus-stops"}
    for entry in verified.values():
        assert entry.status == "metadata_verified"
        assert entry.api_endpoint is not None
        assert entry.dataset_id is not None
        assert entry.api_endpoint == (
            f"https://data.ntpc.gov.tw/api/datasets/{entry.dataset_id}/json"
        )
        assert entry.declared_fields is not None
        # Nobody has seen live rows yet; the field map must say so.
        assert entry.declared_fields.unverified_until_live_fetch is True


def test_duplicate_source_ids_are_refused() -> None:
    with pytest.raises(ValidationError):
        registry_with(entry_payload(), entry_payload(title="Duplicate id"))


def test_verified_status_requires_endpoint_dataset_and_field_map() -> None:
    with pytest.raises(ValidationError):
        SourceEntry.model_validate(entry_payload(status="metadata_verified"))
    with pytest.raises(ValidationError):
        SourceEntry.model_validate(
            entry_payload(
                status="metadata_verified",
                api_endpoint="https://data.ntpc.gov.tw/api/datasets/abc/json",
                dataset_id="abc",
            )
        )


def test_endpoint_must_reference_the_dataset_id_and_be_https() -> None:
    with pytest.raises(ValidationError):
        SourceEntry.model_validate(
            entry_payload(
                api_endpoint="https://data.ntpc.gov.tw/api/datasets/other/json",
                dataset_id="abc",
            )
        )
    with pytest.raises(ValidationError):
        SourceEntry.model_validate(entry_payload(portal_url="http://data.ntpc.gov.tw/datasets/x"))


def test_malformed_registry_file_is_a_typed_refusal(tmp_path: Path) -> None:
    broken = tmp_path / "registry.json"
    broken.write_text('{"registry_id": "x", "entries": []}', encoding="utf-8")
    with pytest.raises(ServiceFault) as fault:
        load_registry(broken)
    assert fault.value.problem.code == ServiceErrorCode.VALIDATION

    with pytest.raises(ServiceFault):
        load_registry(tmp_path / "missing.json")


def test_update_status_returns_a_new_registry_and_keeps_the_original() -> None:
    registry = load_registry(REGISTRY_PATH)

    updated = update_status(
        registry,
        "ntpc-parks",
        status="data_verified",
        reason="Live fetch on 2026-09-12 returned parseable rows.",
        checked_at="2026-09-12T23:00:00+08:00",
    )

    assert get_entry(registry, "ntpc-parks").status == "metadata_verified"
    assert get_entry(updated, "ntpc-parks").status == "data_verified"
    assert get_entry(updated, "ntpc-bus-stops") == get_entry(registry, "ntpc-bus-stops")
    # The helper's JSON is what the integrator persists; it must round-trip.
    reloaded = SourceRegistry.model_validate_json(registry_json(updated))
    assert reloaded == updated


def test_update_status_refuses_skipped_transitions() -> None:
    registry = registry_with(entry_payload())

    with pytest.raises(ServiceFault) as fault:
        update_status(
            registry, "ntpc-example", status="enabled", reason="skip", checked_at="2026-09-12"
        )
    assert fault.value.problem.code == ServiceErrorCode.CONFLICT


def test_update_status_allows_falling_to_unavailable_and_relisting() -> None:
    registry = registry_with(entry_payload())

    down = update_status(
        registry,
        "ntpc-example",
        status="unavailable",
        reason="Portal returned HTTP 503 at check time.",
        checked_at="2026-09-12T23:10:00+08:00",
    )
    assert get_entry(down, "ntpc-example").status == "unavailable"

    relisted = update_status(
        down,
        "ntpc-example",
        status="listed",
        reason="Re-listed for a fresh verification pass.",
        checked_at="2026-09-12T23:20:00+08:00",
    )
    assert get_entry(relisted, "ntpc-example").status == "listed"


def test_update_status_allows_same_status_recheck_refresh() -> None:
    registry = registry_with(entry_payload())

    refreshed = update_status(
        registry,
        "ntpc-example",
        status="listed",
        reason="Re-checked; still only listed.",
        checked_at="2026-09-13T00:00:00+08:00",
    )

    entry = get_entry(refreshed, "ntpc-example")
    assert entry.status == "listed"
    assert entry.status_reason == "Re-checked; still only listed."
    assert entry.checked_at == "2026-09-13T00:00:00+08:00"


def test_unknown_source_id_is_not_found() -> None:
    registry = registry_with(entry_payload())

    with pytest.raises(ServiceFault) as fault:
        get_entry(registry, "nope")
    assert fault.value.problem.code == ServiceErrorCode.NOT_FOUND

    with pytest.raises(ServiceFault) as update_fault:
        update_status(registry, "nope", status="listed", reason="r", checked_at="t")
    assert update_fault.value.problem.code == ServiceErrorCode.NOT_FOUND
