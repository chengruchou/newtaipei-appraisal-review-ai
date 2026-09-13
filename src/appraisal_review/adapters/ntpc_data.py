"""NTPC open-data dataset client over an injected SSRF-guarded reader.

The endpoint comes from a registry SourceEntry, never from code; all network
traffic goes through the injected reader (SafeSourceReader-compatible), so unit
tests run entirely on canned fixtures. Rows are returned as raw dicts exactly
as the platform serves them: this adapter asserts nothing about field meanings,
because nobody has seen live rows until the integrator's real fetch. Every
fetch returns FetchEvidence so a candidate built from a row can cite the exact
payload it came from.

Content verification is layered honestly: a reader that exposes a
``content_type`` attribute on its documents (the test fixtures do) has the
declared media type checked here; the project's SafeSourceReader does not
surface response headers (it refuses declared binary types itself), so with it
the declared type is recorded as "undeclared" and the strict JSON array parse
below is the effective verification.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import Field

from appraisal_review.application.source_lookup import SourceReadRefused
from appraisal_review.application.source_registry import (
    FETCHABLE_STATUSES,
    SourceEntry,
)
from appraisal_review.domain.document_models import Digest
from appraisal_review.domain.service_contracts import OpaqueID, ServiceModel

#: The only host this adapter will ever build a dataset URL for.
NTPC_DATA_HOST = "data.ntpc.gov.tw"

_JSON_MEDIA_TYPES = frozenset({"application/json", "text/json"})
_MAX_PAGE_SIZE = 1000


class SourceNotFetchable(SourceReadRefused):
    """The registry entry's status does not permit fetching rows."""


class WrongContentTypeRefused(SourceReadRefused):
    """The response declared a non-JSON media type."""


class TruncatedPayloadRefused(SourceReadRefused):
    """The reader truncated the payload; a partial JSON body is not data."""


class NonJsonPayloadRefused(SourceReadRefused):
    """The payload did not parse as a JSON array of row objects."""


class RetrievedDocumentLike(Protocol):
    """Structural view of SafeSourceReader's RetrievedDocument."""

    @property
    def url(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def truncated(self) -> bool: ...


class SourceReaderPort(Protocol):
    """SafeSourceReader-compatible transport seam; tests inject fakes here."""

    def read(
        self, url: str, *, max_chars: int | None = None, max_seconds: float | None = None
    ) -> RetrievedDocumentLike: ...


class FetchEvidence(ServiceModel):
    """Exact provenance of one fetched payload, for candidate citations.

    sha256 is computed over the UTF-8 encoding of the text the reader returned
    (the reader decodes bytes with replacement; a payload that parses as JSON
    was decoded losslessly).
    """

    source_id: OpaqueID
    url: str = Field(min_length=1, max_length=1000)
    retrieved_at: int = Field(ge=0, strict=True)
    sha256: Digest
    row_count: int = Field(ge=0, strict=True)
    content_type: str = Field(min_length=1, max_length=200)


class NtpcDatasetClient:
    """Fetch raw dataset rows for one registry entry through an injected reader."""

    def __init__(
        self,
        reader: SourceReaderPort,
        entry: SourceEntry,
        *,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        if entry.api_endpoint is None or entry.dataset_id is None:
            raise ValueError("The registry entry carries no API endpoint to fetch")
        parts = urlsplit(entry.api_endpoint)
        if parts.scheme.lower() != "https" or (parts.hostname or "").lower() != NTPC_DATA_HOST:
            raise ValueError(f"The API endpoint must be https on {NTPC_DATA_HOST}")
        if parts.query or parts.fragment:
            raise ValueError("The configured endpoint must not carry a query or fragment")
        self._reader = reader
        self._entry = entry
        self._clock = clock

    @property
    def entry(self) -> SourceEntry:
        return self._entry

    def fetch_rows(
        self, page: int = 0, size: int = _MAX_PAGE_SIZE, *, timeout_seconds: float | None = None
    ) -> tuple[tuple[dict[str, Any], ...], FetchEvidence]:
        """Return (raw rows, evidence) for one page, or raise a typed refusal.

        Rows are the platform's own JSON objects, unmapped: interpreting fields
        is the caller's job, guided by the entry's declared_fields config which
        stays unverified until a live payload has been inspected.
        """
        if type(page) is not int or page < 0:
            raise ValueError("page must be a non-negative int")
        if type(size) is not int or not 1 <= size <= _MAX_PAGE_SIZE:
            raise ValueError(f"size must be an int between 1 and {_MAX_PAGE_SIZE}")
        if self._entry.status not in FETCHABLE_STATUSES:
            raise SourceNotFetchable(
                f"source {self._entry.source_id} has status {self._entry.status}; "
                "only a metadata_verified, data_verified or enabled source may be fetched"
            )
        url = f"{self._entry.api_endpoint}?page={page}&size={size}"
        document = self._reader.read(url, max_seconds=timeout_seconds)
        if document.truncated:
            raise TruncatedPayloadRefused(f"payload from {document.url} was truncated")
        declared = getattr(document, "content_type", None)
        if declared is not None:
            media_type = str(declared).split(";", 1)[0].strip().lower()
            if media_type not in _JSON_MEDIA_TYPES and not media_type.endswith("+json"):
                raise WrongContentTypeRefused(
                    f"refusing declared content type {media_type or '(empty)'} from {document.url}"
                )
        try:
            payload = json.loads(document.text)
        except ValueError as error:
            raise NonJsonPayloadRefused(f"payload from {document.url} is not JSON") from error
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise NonJsonPayloadRefused(
                f"payload from {document.url} is not a JSON array of row objects"
            )
        rows: tuple[dict[str, Any], ...] = tuple(payload)
        evidence = FetchEvidence(
            source_id=self._entry.source_id,
            url=document.url,
            retrieved_at=self._clock(),
            sha256=hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
            row_count=len(rows),
            content_type=str(declared) if declared is not None else "undeclared",
        )
        return rows, evidence
