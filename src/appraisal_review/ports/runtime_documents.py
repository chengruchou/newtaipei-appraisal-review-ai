"""Current authorized source bytes and immutable run binding for local execution.

C2 implementations require sanitized admission. A separately configured loopback
original-document implementation may satisfy this port without granting C2 export
authority. Neither implementation accepts request-selected paths or URLs.
"""

from typing import Protocol

from appraisal_review.application.service_guards import Principal
from appraisal_review.domain.service_contracts import (
    DocumentReference,
    MaterialRevision,
    RunReference,
)
from appraisal_review.ports.document_transfer import DocumentAuthorization


class RuntimeDocumentBytes(Protocol):
    @property
    def content(self) -> bytes: ...


class RuntimeDocuments(Protocol):
    authorization: DocumentAuthorization

    def read(self, principal: Principal, reference: DocumentReference) -> RuntimeDocumentBytes: ...

    def create_snapshot(
        self, principal: Principal, run: RunReference, revision: MaterialRevision
    ) -> object: ...

    def read_snapshot(
        self, principal: Principal, run: RunReference, reference: DocumentReference
    ) -> RuntimeDocumentBytes: ...
