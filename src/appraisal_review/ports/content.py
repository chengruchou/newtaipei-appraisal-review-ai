"""Authorized byte delivery for sources and published artifacts.

The two content routes used to be declared inside the local composition root, so the
exported OpenAPI document - and therefore the generated browser client - did not describe
them at all. A reviewer could download an artifact the contract never mentioned. This port
moves the shape into the shared contract and leaves every local specific (catalog lookup,
document transfer, publication resolver) behind the adapter.

Delivery is deliberately narrow: the adapter is handed an already-authenticated principal
and returns bytes with the media type it actually produced. The route never guesses an
extension, so adding the official spreadsheet output does not mean editing transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from appraisal_review.application.service_guards import Principal

# Only formats this system actually writes. An open media type here would let a storage
# adapter choose what a browser renders inline, which is a content-injection surface.
ContentMediaType = Literal[
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]


@dataclass(frozen=True)
class DeliveredContent:
    """Verified bytes plus the media type the producing adapter recorded for them."""

    data: bytes
    media_type: ContentMediaType
    filename: str | None = None

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("Delivered content must carry bytes")
        if self.filename is not None and ("/" in self.filename or "\\" in self.filename):
            # The filename reaches a Content-Disposition header; a separator there invites
            # a path-traversal reading by whatever writes the download to disk.
            raise ValueError("A delivered filename must not contain path separators")


class ContentPlane(Protocol):
    """Reads authorized bytes. Implementations verify integrity before returning."""

    async def read_source(
        self,
        principal: Principal,
        *,
        document_id: UUID,
        version: str,
        content_hash: str,
    ) -> DeliveredContent:
        """Return an admitted source document, bound to its exact version and hash."""
        ...

    async def read_artifact(
        self,
        principal: Principal,
        *,
        job_id: UUID,
        artifact_id: UUID,
    ) -> DeliveredContent:
        """Return a published artifact for the job's current run, or raise for unpublished."""
        ...
