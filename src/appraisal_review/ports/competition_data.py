"""Trusted local classification/authorization ports, never supplied by a request."""

from typing import Protocol

from appraisal_review.domain.competition_data import DataPart, DataReviewRecord


class CompetitionDataAdmission(Protocol):
    def check(self, parts: tuple[DataPart, ...]) -> None:
        """Deny before transport unless the exact complete envelope is currently admitted."""
        ...


class DataReviewAuthority(Protocol):
    def current(self, envelope_sha256: str) -> DataReviewRecord | None: ...

    def permits(self, record: DataReviewRecord) -> bool:
        """Verify the exact approved record, current revocation and trusted reviewer scope."""
        ...
