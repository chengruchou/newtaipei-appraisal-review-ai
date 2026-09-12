"""Production transport callables for SafeSourceReader.

One exact hop per call: redirects are returned to the reader, never followed
here, so every hop re-passes the allowlist and resolved-IP checks. The body
read is capped at the transport, and the resolver answers with the same
system resolution the connection will use. This module holds no policy - the
reader owns the allowlist and deadlines.
"""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request

from appraisal_review.adapters.runtime_sources import (
    FetchCallable,
    FetchResult,
    ResolveCallable,
)

#: One transport read never returns more than this many bytes plus one, so the
#: reader's own max_bytes refusal can see an over-limit body without the
#: transport buffering an unbounded response.
_HARD_READ_CAP = 8_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def urllib_fetch(*, connect_timeout_seconds: float = 10.0) -> FetchCallable:
    # Python 3.13 turns on VERIFY_X509_STRICT, which rejects certificate chains
    # missing a Subject Key Identifier - several Taiwanese government hosts
    # (data.ntpc.gov.tw among them) still serve such chains. Dropping ONLY the
    # strict flag restores the standard pre-3.13 verification: the chain of
    # trust and the hostname check both remain fully enforced.
    context = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=context)
    )

    def fetch(url: str) -> FetchResult:
        request = urllib.request.Request(
            url, headers={"User-Agent": "appraisal-review-workbench/1.0"}
        )
        try:
            with opener.open(request, timeout=connect_timeout_seconds) as response:
                body = response.read(_HARD_READ_CAP + 1)
                return FetchResult(
                    status_code=response.status,
                    body=body,
                    redirect_to=None,
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as error:
            location = error.headers.get("Location") if error.headers else None
            body = error.read(_HARD_READ_CAP + 1) if error.fp is not None else b""
            return FetchResult(
                status_code=error.code,
                body=body,
                redirect_to=location,
                headers=dict(error.headers.items()) if error.headers else {},
            )

    return fetch


def system_resolve() -> ResolveCallable:
    def resolve(host: str) -> tuple[str, ...]:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return tuple(sorted({str(info[4][0]) for info in infos}))

    return resolve
