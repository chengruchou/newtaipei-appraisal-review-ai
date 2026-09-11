"""Finite local diagnostic vocabulary; no document or operator values."""

from enum import StrEnum


class RestoreStage(StrEnum):
    REQUEST = "request"
    SOURCE = "source"
    MAPPING = "mapping"
    PUBLICATION = "publication"
    AUTHORITY = "authority"
    PLAN = "plan"
    RENDER = "render"
    OCR = "ocr"
    OCR_VALIDATION = "ocr_validation"
    AUTOMATIC_RESTORE = "automatic_restore"
    REVIEW = "review"
    REVIEW_LIFETIME = "review_lifetime"
    ENGINE = "engine"
    REVIEW_AUTHORITY = "review_authority"
    REVIEW_EVIDENCE = "review_evidence"
    CANDIDATE_WRITE = "candidate_write"
    FINAL_WRITE = "final_write"
    FINAL_READ = "final_read"
    FILE_READ = "file_read"


class RestoreFailureCode(StrEnum):
    REQUEST_REJECTED = "request_rejected"
    VALIDATION_FAILED = "validation_failed"
    AUTHORITY_DENIED = "authority_denied"
    REVIEW_EXPIRED = "review_expired"
    REVIEW_REVOKED = "review_revoked"
    ENGINE_CHANGED = "engine_changed"
    EVIDENCE_CHANGED = "evidence_changed"
    LOCAL_IO_FAILED = "local_io_failed"
    OPERATION_TIMED_OUT = "operation_timed_out"
    OPERATION_FAILED = "operation_failed"


class LocalRestoreFailure(ValueError):
    """A known guard outcome; the diagnostic never serializes exception text."""

    def __init__(self, code: RestoreFailureCode, message: str | None = None) -> None:
        self.diagnostic_code = code
        super().__init__(message if message is not None else code.value)
