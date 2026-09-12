"""Trusted, exact-source processing scope; never a case or calculation approval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from appraisal_review.domain.service_contracts import DocumentReference


@dataclass(frozen=True)
class SourceProcessingScope:
    """Resolved source configuration supplied by trusted server composition.

    This is an internal configuration record, not an upload/request DTO. A/C map
    their source catalog to exact references after resolving document identities.
    No scope is inferred from a filename, upload date, document text or client flag.
    """

    scope_id: str
    version: str
    documents: tuple[DocumentReference, ...]
    privacy_handling: Literal["not_applicable"] = "not_applicable"
    basis: Literal["confirmed_standard_formula_documents"] = "confirmed_standard_formula_documents"

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None
            for value in (self.scope_id, self.version)
        ):
            raise ValueError("Source processing scope requires stable identity and version")
        if (
            self.privacy_handling != "not_applicable"
            or self.basis != "confirmed_standard_formula_documents"
        ):
            raise ValueError("Source scope cannot assert a privacy scan or approval result")
        if not isinstance(self.documents, tuple) or not 1 <= len(self.documents) <= 100:
            raise ValueError("Source scope requires an explicit bounded source set")
        documents = tuple(
            DocumentReference.model_validate_json(document.model_dump_json())
            for document in self.documents
        )
        if len({document.case_id for document in documents}) != 1:
            raise ValueError("Source processing scope must bind one exact case")
        if len({document.document_id for document in documents}) != len(documents):
            raise ValueError("Source processing scope cannot contain duplicate document identities")
        object.__setattr__(self, "documents", tuple(sorted(documents, key=lambda d: d.document_id)))

    def matches(self, documents: tuple[DocumentReference, ...]) -> bool:
        """Every case/document/version/hash/purpose must match; extras never inherit scope."""
        return self.documents == tuple(sorted(documents, key=lambda d: d.document_id))
