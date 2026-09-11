"""Server-configured local review records; this adapter cannot approve new records."""

from collections.abc import Callable, Mapping

from appraisal_review.domain.competition_data import DataReviewRecord, record_digest


class PinnedDataReviewAuthority:
    def __init__(
        self,
        records: tuple[DataReviewRecord, ...],
        trusted_record_digests: frozenset[str],
        reviewer_envelopes: Mapping[str, frozenset[str]],
        *,
        revoked: Callable[[str], bool],
    ) -> None:
        self._records = {
            r.envelope_sha256: DataReviewRecord.model_validate_json(r.model_dump_json())
            for r in records
        }
        if len(self._records) != len(records):
            raise ValueError("Ambiguous envelope reviews")
        self._trusted = trusted_record_digests
        self._reviewers = dict(reviewer_envelopes)
        self._revoked = revoked

    def current(self, envelope_sha256: str) -> DataReviewRecord | None:
        record = self._records.get(envelope_sha256)
        return record.model_copy(deep=True) if record is not None else None

    def permits(self, record: DataReviewRecord) -> bool:
        digest = record_digest(record)
        return (
            digest in self._trusted
            and record.envelope_sha256 in self._reviewers.get(record.reviewer_id, frozenset())
            and self._records.get(record.envelope_sha256) == record
            and self._revoked(digest) is False
        )
