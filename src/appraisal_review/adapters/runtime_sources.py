"""SSRF-safe reading of official web sources. NOT wired into the app.

SafeSourceReader fetches only https URLs on a caller-supplied allowlist,
re-validating every redirect hop and refusing private, loopback, link-local
and metadata addresses after DNS resolution. Network and DNS are injected
callables so unit tests never touch the real network. There are no
hardcoded case answers anywhere in this module.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from appraisal_review.application.source_lookup import (
    SearchUnavailable as SearchUnavailable,
)
from appraisal_review.application.source_lookup import (
    SourceReadRefused as SourceReadRefused,
)


class SchemeRefused(SourceReadRefused):
    """URL scheme is not https."""


class HostNotAllowlisted(SourceReadRefused):
    """URL host is not on the configured source allowlist."""


class PrivateAddressRefused(SourceReadRefused):
    """Host resolved to a private, loopback, link-local or metadata address."""


class RedirectRefused(SourceReadRefused):
    """A redirect target failed validation or too many hops occurred."""


class HttpStatusRefused(SourceReadRefused):
    """Response carried a non-success HTTP status; its body is not a document."""


class IncompleteContentRefused(HttpStatusRefused):
    """204/206/304 responses cannot yield a complete document here."""


class UnsupportedContentTypeRefused(SourceReadRefused):
    """Declared Content-Type is a binary format; refusing to decode it as text."""


class OversizeBodyRefused(SourceReadRefused):
    """Response body exceeded max_bytes."""


class ReadTimedOut(SourceReadRefused):
    """Wall-clock time for the read exceeded max_seconds."""


@dataclass(frozen=True)
class SearchResult:
    """One hit from an official-source search."""

    result_id: str
    source_id: str
    title: str
    url: str
    snippet: str = ""


class SearchProvider(Protocol):
    """Port for a real search capability over catalogued official sources."""

    def search(
        self, query: str, source_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[SearchResult, ...]:
        """Return results or raise SearchUnavailable; never invent hits.

        timeout_seconds, when given, is the wall-clock budget remaining for
        this one call; the transport must not run longer than that.
        """
        ...


class NullSearchProvider:
    """Honest default until a real provider exists: always unavailable."""

    def search(
        self, query: str, source_id: str, *, timeout_seconds: float | None = None
    ) -> tuple[SearchResult, ...]:
        raise SearchUnavailable("no search provider is configured; refusing to fabricate results")


@dataclass(frozen=True)
class FixedSourceDocument:
    """A known document at a fixed URL inside one catalogued source."""

    result_id: str
    source_id: str
    title: str
    url: str


class FixedUrlCatalog:
    """Narrow capability: list known fixed-URL documents per source.

    This is deliberately NOT a SearchProvider. Enumerating a hand-curated
    catalog must never be presented to the model or to reviewers as if a
    query had been executed against the source.
    """

    def __init__(self, documents: Iterable[FixedSourceDocument]) -> None:
        self._by_source: dict[str, tuple[FixedSourceDocument, ...]] = {}
        for doc in documents:
            existing = self._by_source.get(doc.source_id, ())
            self._by_source[doc.source_id] = (*existing, doc)

    def documents_for(self, source_id: str) -> tuple[FixedSourceDocument, ...]:
        return self._by_source.get(source_id, ())


@dataclass(frozen=True)
class FetchResult:
    """Injected fetcher's view of one HTTP response (one hop, no auto-follow)."""

    status_code: int
    body: bytes = b""
    redirect_to: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


FetchCallable = Callable[[str], FetchResult]
ResolveCallable = Callable[[str], Sequence[str]]

# Only these statuses may carry an honored redirect_to; an error response
# carrying redirect_to must never bypass status validation.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Success-family statuses that still cannot represent a complete document:
# 204 has no body, 206 is a partial body, 304 needs a cached copy we lack.
_INCOMPLETE_STATUSES = frozenset({204, 206, 304})
# Declared types that are clearly binary and must not be decoded as UTF-8
# text. HTML is deliberately NOT blocked: official sources are HTML.
_BINARY_CONTENT_TYPES = frozenset(
    {"application/pdf", "application/octet-stream", "application/zip"}
)
_BINARY_CONTENT_PREFIXES = ("image/", "audio/", "video/", "font/")


@dataclass(frozen=True)
class RetrievedDocument:
    """Successfully read source text plus the final URL it came from."""

    url: str
    text: str
    truncated: bool


class SafeSourceReader:
    """Reads https documents from allowlisted hosts with SSRF guards.

    The allowlist is policy configuration passed by the caller; nothing is
    hardcoded. `fetch` must perform exactly one hop (no automatic redirect
    following) and `resolve` must return the IP addresses the fetch will
    actually connect to, so validation and connection see the same answer.
    """

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        fetch: FetchCallable,
        resolve: ResolveCallable,
        max_bytes: int = 2_000_000,
        max_seconds: float = 20.0,
        max_redirects: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._allowed_hosts = frozenset(host.strip().lower() for host in allowed_hosts)
        self._fetch = fetch
        self._resolve = resolve
        self._max_bytes = max_bytes
        self._max_seconds = max_seconds
        self._max_redirects = max_redirects
        self._clock = clock

    def read(
        self, url: str, *, max_chars: int | None = None, max_seconds: float | None = None
    ) -> RetrievedDocument:
        # A per-call override can only tighten the configured cap, never
        # loosen it: the effective deadline is min(instance cap, override).
        limit = self._max_seconds if max_seconds is None else min(self._max_seconds, max_seconds)
        started = self._clock()
        current = url
        self._validate_url(current)
        for _hop in range(self._max_redirects + 1):
            self._check_deadline(started, limit)
            response = self._fetch(current)
            self._check_deadline(started, limit)
            status = response.status_code
            if status in _REDIRECT_STATUSES:
                if response.redirect_to is None:
                    raise RedirectRefused(
                        f"redirect status {status} from {current} carries no target"
                    )
                try:
                    self._validate_url(response.redirect_to)
                except SourceReadRefused as refusal:
                    raise RedirectRefused(
                        f"redirect from {current} to refused target "
                        f"{response.redirect_to}: {refusal}"
                    ) from refusal
                current = response.redirect_to
                continue
            if status in _INCOMPLETE_STATUSES:
                raise IncompleteContentRefused(
                    f"HTTP {status} from {current} does not carry a complete document"
                )
            if not 200 <= status < 300:
                raise HttpStatusRefused(f"refusing HTTP status {status} from {current}")
            self._refuse_binary_content_type(response.headers, current)
            if len(response.body) > self._max_bytes:
                raise OversizeBodyRefused(f"body of {current} exceeds {self._max_bytes} bytes")
            text = response.body.decode("utf-8", errors="replace")
            truncated = max_chars is not None and len(text) > max_chars
            if truncated and max_chars is not None:
                text = text[:max_chars]
            return RetrievedDocument(url=current, text=text, truncated=truncated)
        raise RedirectRefused(f"more than {self._max_redirects} redirects from {url}")

    def _refuse_binary_content_type(self, headers: Mapping[str, str], url: str) -> None:
        # Refusal messages carry only the media type and URL for diagnosis,
        # never header values (they may hold credentials).
        declared = next(
            (value for name, value in headers.items() if name.lower() == "content-type"), ""
        )
        media_type = declared.split(";", 1)[0].strip().lower()
        if not media_type:
            return
        if media_type in _BINARY_CONTENT_TYPES or media_type.startswith(_BINARY_CONTENT_PREFIXES):
            raise UnsupportedContentTypeRefused(
                f"refusing to decode binary content type {media_type} from {url}"
            )

    def _check_deadline(self, started: float, limit: float) -> None:
        if self._clock() - started > limit:
            raise ReadTimedOut(f"read exceeded {limit} seconds")

    def _validate_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme.lower() != "https":
            raise SchemeRefused(f"refusing non-https scheme: {parts.scheme or '(none)'}")
        host = (parts.hostname or "").lower()
        if not host or host not in self._allowed_hosts:
            raise HostNotAllowlisted(f"host not on source allowlist: {host or '(none)'}")
        addresses = self._resolve(host)
        if not addresses:
            raise PrivateAddressRefused(f"host {host} did not resolve to any address")
        for raw in addresses:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError as error:
                raise PrivateAddressRefused(
                    f"host {host} resolved to unparsable address {raw!r}"
                ) from error
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
                or address.is_unspecified
            ):
                raise PrivateAddressRefused(f"host {host} resolved to non-public address {address}")
