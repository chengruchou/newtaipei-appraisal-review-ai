"""NTPC dataset client regressions: canned transport only, no real network."""

import hashlib
import json
from dataclasses import dataclass

import pytest

from appraisal_review.adapters.ntpc_data import (
    NonJsonPayloadRefused,
    NtpcDatasetClient,
    SourceNotFetchable,
    TruncatedPayloadRefused,
    WrongContentTypeRefused,
)
from appraisal_review.adapters.runtime_sources import (
    FetchResult,
    HostNotAllowlisted,
    SafeSourceReader,
)
from appraisal_review.application.source_lookup import SourceReadRefused
from appraisal_review.application.source_registry import SourceEntry

NOW = 1_757_600_000
PARKS_ID = "5fe3a136-29cc-4695-a17e-6636a32c3342"
PARK_ROWS = [
    {"name": "四維公園", "latitude": "25.0132", "longitude": "121.4623", "area": "18000"},
    {"name": "板橋第一運動場", "latitude": "25.0221", "longitude": "121.4534", "area": "52000"},
]


def parks_entry(status: str = "metadata_verified", **overrides: object) -> SourceEntry:
    payload: dict[str, object] = {
        "source_id": "ntpc-parks",
        "title": "新北市公園",
        "publisher": "新北市政府資料開放平台 (data.ntpc.gov.tw)",
        "portal_url": f"https://data.ntpc.gov.tw/datasets/{PARKS_ID}",
        "status": status,
        "status_reason": "unit test entry",
        "checked_at": "2026-09-12T22:30:00+08:00",
        "api_endpoint": f"https://data.ntpc.gov.tw/api/datasets/{PARKS_ID}/json",
        "dataset_id": PARKS_ID,
        "declared_fields": {
            "name_field": "name",
            "lat_field": "latitude",
            "lng_field": "longitude",
            "unverified_until_live_fetch": True,
        },
    }
    payload.update(overrides)
    return SourceEntry.model_validate(payload)


@dataclass(frozen=True)
class Document:
    """Reader document exposing the declared content type (richer than RetrievedDocument)."""

    url: str
    text: str
    truncated: bool = False
    content_type: str | None = "application/json; charset=utf-8"


class FakeReader:
    def __init__(self, document: Document | Exception) -> None:
        self.document = document
        self.calls: list[tuple[str, float | None]] = []

    def read(
        self, url: str, *, max_chars: int | None = None, max_seconds: float | None = None
    ) -> Document:
        self.calls.append((url, max_seconds))
        if isinstance(self.document, Exception):
            raise self.document
        return self.document


def json_document(payload: object, url: str = "https://data.ntpc.gov.tw/final") -> Document:
    return Document(url=url, text=json.dumps(payload, ensure_ascii=False))


def test_happy_path_returns_raw_rows_and_exact_evidence() -> None:
    document = json_document(PARK_ROWS)
    reader = FakeReader(document)
    client = NtpcDatasetClient(reader, parks_entry(), clock=lambda: NOW)

    rows, evidence = client.fetch_rows(page=2, size=50, timeout_seconds=7.5)

    assert rows == tuple(PARK_ROWS)
    assert reader.calls == [
        (f"https://data.ntpc.gov.tw/api/datasets/{PARKS_ID}/json?page=2&size=50", 7.5)
    ]
    assert evidence.source_id == "ntpc-parks"
    assert evidence.url == document.url
    assert evidence.retrieved_at == NOW
    assert evidence.row_count == 2
    assert evidence.content_type == "application/json; charset=utf-8"
    assert evidence.sha256 == hashlib.sha256(document.text.encode("utf-8")).hexdigest()


def test_happy_path_through_a_real_safe_source_reader() -> None:
    body = json.dumps(PARK_ROWS, ensure_ascii=False).encode("utf-8")
    expected_url = f"https://data.ntpc.gov.tw/api/datasets/{PARKS_ID}/json?page=0&size=1000"

    def fetch(url: str) -> FetchResult:
        assert url == expected_url
        return FetchResult(status_code=200, body=body, headers={"Content-Type": "application/json"})

    reader = SafeSourceReader(
        allowed_hosts=["data.ntpc.gov.tw"], fetch=fetch, resolve=lambda host: ["8.8.8.8"]
    )
    client = NtpcDatasetClient(reader, parks_entry(), clock=lambda: NOW)

    rows, evidence = client.fetch_rows()

    assert rows == tuple(PARK_ROWS)
    assert evidence.url == expected_url
    assert evidence.sha256 == hashlib.sha256(body).hexdigest()
    # SafeSourceReader does not surface response headers to the adapter.
    assert evidence.content_type == "undeclared"


def test_reader_refusals_propagate_unchanged() -> None:
    refusal = HostNotAllowlisted("host not on source allowlist: evil.example")
    client = NtpcDatasetClient(FakeReader(refusal), parks_entry(), clock=lambda: NOW)

    with pytest.raises(HostNotAllowlisted):
        client.fetch_rows()
    with pytest.raises(SourceReadRefused):
        NtpcDatasetClient(FakeReader(refusal), parks_entry()).fetch_rows()


def test_non_json_body_is_a_typed_refusal() -> None:
    client = NtpcDatasetClient(
        FakeReader(Document(url="https://data.ntpc.gov.tw/x", text="<html>error page</html>")),
        parks_entry(),
        clock=lambda: NOW,
    )
    with pytest.raises(NonJsonPayloadRefused):
        client.fetch_rows()


def test_json_that_is_not_a_list_of_objects_is_refused() -> None:
    for payload in ({"rows": PARK_ROWS}, ["just", "strings"], 42):
        client = NtpcDatasetClient(
            FakeReader(json_document(payload)), parks_entry(), clock=lambda: NOW
        )
        with pytest.raises(NonJsonPayloadRefused):
            client.fetch_rows()


def test_declared_non_json_content_type_is_refused() -> None:
    document = Document(
        url="https://data.ntpc.gov.tw/x",
        text=json.dumps(PARK_ROWS),
        content_type="text/html; charset=utf-8",
    )
    client = NtpcDatasetClient(FakeReader(document), parks_entry(), clock=lambda: NOW)
    with pytest.raises(WrongContentTypeRefused):
        client.fetch_rows()


def test_truncated_payload_is_refused() -> None:
    document = Document(
        url="https://data.ntpc.gov.tw/x", text=json.dumps(PARK_ROWS)[:20], truncated=True
    )
    client = NtpcDatasetClient(FakeReader(document), parks_entry(), clock=lambda: NOW)
    with pytest.raises(TruncatedPayloadRefused):
        client.fetch_rows()


def test_listed_source_cannot_be_fetched() -> None:
    entry = parks_entry(
        status="listed",
        # A listed entry may still carry endpoint config; status is what gates.
    )
    client = NtpcDatasetClient(FakeReader(json_document(PARK_ROWS)), entry, clock=lambda: NOW)
    with pytest.raises(SourceNotFetchable):
        client.fetch_rows()


def test_entry_without_endpoint_or_off_host_endpoint_is_refused_at_construction() -> None:
    reader = FakeReader(json_document(PARK_ROWS))
    with pytest.raises(ValueError):
        NtpcDatasetClient(reader, parks_entry(status="listed", api_endpoint=None, dataset_id=None))
    with pytest.raises(ValueError):
        NtpcDatasetClient(
            reader,
            parks_entry(
                api_endpoint=f"https://evil.example/api/datasets/{PARKS_ID}/json",
            ),
        )
    with pytest.raises(ValueError):
        NtpcDatasetClient(
            reader,
            parks_entry(
                api_endpoint=(f"https://data.ntpc.gov.tw/api/datasets/{PARKS_ID}/json?size=9999"),
            ),
        )


def test_invalid_page_and_size_are_refused_before_any_read() -> None:
    reader = FakeReader(json_document(PARK_ROWS))
    client = NtpcDatasetClient(reader, parks_entry(), clock=lambda: NOW)

    with pytest.raises(ValueError):
        client.fetch_rows(page=-1)
    with pytest.raises(ValueError):
        client.fetch_rows(size=0)
    with pytest.raises(ValueError):
        client.fetch_rows(size=1001)
    assert reader.calls == []
