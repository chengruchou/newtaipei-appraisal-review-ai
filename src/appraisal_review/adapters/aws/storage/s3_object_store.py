"""Narrow S3 object transfer around an injected synchronous client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from appraisal_review.domain.pdf_models import (
    PDFReadError,
    PDFWriteError,
    UnsupportedDocumentURIError,
    document_identity,
)


class S3TransferClient(Protocol):
    """The subset of a boto3-compatible S3 client used by this adapter."""

    def download_file(self, Bucket: str, Key: str, Filename: str) -> Any: ...

    def put_object(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class S3Location:
    bucket: str
    key: str

    @classmethod
    def from_uri(cls, uri: str) -> S3Location:
        identity = document_identity(uri)
        if identity[0] != "s3":
            raise UnsupportedDocumentURIError("S3 object access requires an S3 URI")
        return cls(bucket=identity[1], key=identity[2])


class S3ObjectStore:
    """Download and upload literal S3 keys without creating a client."""

    def __init__(
        self,
        client: S3TransferClient,
        *,
        overwrite_existing: bool = False,
    ) -> None:
        self.client = client
        self.overwrite_existing = overwrite_existing

    def download(self, source_uri: str, destination_path: Path) -> None:
        location = S3Location.from_uri(source_uri)
        try:
            if not destination_path.parent.is_dir():
                raise PDFReadError("Local S3 download parent is unavailable")
            self.client.download_file(
                Bucket=location.bucket,
                Key=location.key,
                Filename=str(destination_path),
            )
            if not destination_path.is_file() or destination_path.stat().st_size == 0:
                raise PDFReadError("S3 source download did not create a file")
        except PDFReadError:
            raise
        except Exception as error:
            raise PDFReadError("S3 PDF source could not be downloaded") from error

    def upload_pdf(self, source_path: Path, destination_uri: str) -> None:
        location = S3Location.from_uri(destination_uri)
        try:
            if not source_path.is_file() or source_path.stat().st_size == 0:
                raise PDFWriteError("Validated local PDF output is unavailable")
            request: dict[str, Any] = {
                "Bucket": location.bucket,
                "Key": location.key,
                "ContentType": "application/pdf",
            }
            if not self.overwrite_existing:
                request["IfNoneMatch"] = "*"
            with source_path.open("rb") as source:
                self.client.put_object(Body=source, **request)
        except PDFWriteError:
            raise
        except Exception as error:
            raise PDFWriteError("Validated PDF output could not be uploaded") from error
