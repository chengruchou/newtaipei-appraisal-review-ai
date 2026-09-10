"""S3 wrapper that delegates all PDF semantics to a validated local writer."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from appraisal_review.adapters.aws.storage.s3_object_store import S3ObjectStore
from appraisal_review.domain.pdf_models import (
    InvalidPDFResultError,
    PDFWriteError,
    PDFWriteRequest,
    PDFWriteResult,
    SourceDestinationConflictError,
    UnsupportedDocumentURIError,
    document_identity,
)
from appraisal_review.ports.pdf import PDFWriter


class S3PDFWriter:
    """Transfer S3 objects around the provider-neutral local PDF writer port."""

    def __init__(self, *, object_store: S3ObjectStore, local_writer: PDFWriter) -> None:
        if bool(getattr(local_writer, "reveals_placeholders", False)):
            raise PDFWriteError("Backfilled placeholder output must never be published")
        self.object_store = object_store
        self.local_writer = local_writer

    @property
    def supports_multiple_contexts(self) -> bool:
        return bool(getattr(self.local_writer, "supports_multiple_contexts", False))

    async def write_pdf(self, request: PDFWriteRequest) -> PDFWriteResult:
        source_location = self.object_store_location(request.source_uri)
        destination_location = self.object_store_location(request.destination_uri)
        if source_location == destination_location:
            raise SourceDestinationConflictError("S3 source and destination must differ")
        protected_locations = {
            self.object_store_location(uri) for uri in request.protected_source_uris
        }
        if destination_location in protected_locations:
            raise SourceDestinationConflictError(
                "S3 destination aliases a protected reviewed source"
            )

        with TemporaryDirectory(prefix="appraisal-pdf-") as raw_directory:
            directory = Path(raw_directory)
            local_source = directory / "source.pdf"
            local_output = directory / "output.pdf"
            self.object_store.download(request.source_uri, local_source)
            local_request = PDFWriteRequest.model_validate(
                {
                    **request.model_dump(),
                    "source_uri": local_source.as_uri(),
                    "destination_uri": local_output.as_uri(),
                    "protected_source_uris": [local_source.as_uri()],
                }
            )
            local_result = await self.local_writer.write_pdf(local_request)
            if not isinstance(local_result, PDFWriteResult):
                raise InvalidPDFResultError("Local PDF writer must return PDFWriteResult")
            validated_result = PDFWriteResult.model_validate(local_result.model_dump())
            expected_ids = [field.field_id for field in request.field_map.fields]
            if (
                document_identity(validated_result.output_uri)
                != document_identity(local_output.as_uri())
                or validated_result.written_field_ids != expected_ids
                or not local_output.is_file()
            ):
                raise InvalidPDFResultError("Local PDF writer result is inconsistent")
            self.object_store.upload_pdf(local_output, request.destination_uri)
            return PDFWriteResult(
                output_uri=request.destination_uri,
                page_count=validated_result.page_count,
                written_field_ids=validated_result.written_field_ids,
                warnings=validated_result.warnings,
            )

    @staticmethod
    def object_store_location(uri: str) -> tuple[str, str, str]:
        identity = document_identity(uri)
        if identity[0] != "s3":
            raise UnsupportedDocumentURIError("S3 PDF writer requires S3 object URIs")
        return identity
