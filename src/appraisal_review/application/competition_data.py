"""Fail-closed competition admission for immutable outbound envelopes."""

from collections.abc import Callable
from datetime import UTC, datetime

from appraisal_review.domain.competition_data import (
    CompetitionDataFault,
    CompetitionDataPolicy,
    DataPart,
    DataReviewRecord,
    envelope_digest,
    record_digest,
)
from appraisal_review.domain.privacy_export import PrivacyExportPayload
from appraisal_review.ports.competition_data import DataReviewAuthority


class ReviewedCompetitionAdmission:
    def __init__(
        self,
        policy: CompetitionDataPolicy,
        trusted_policy_sha256: str,
        authority: DataReviewAuthority,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.policy = CompetitionDataPolicy.model_validate_json(policy.model_dump_json())
        self.trusted_policy_sha256 = trusted_policy_sha256
        self.authority, self.clock = authority, clock

    def check(self, parts: tuple[DataPart, ...]) -> None:
        try:
            if record_digest(self.policy) != self.trusted_policy_sha256:
                raise CompetitionDataFault("competition_data_policy")
            digest = envelope_digest(parts)
            record = self.authority.current(digest)
            if record is None:
                raise CompetitionDataFault("competition_data_unreviewed")
            record = DataReviewRecord.model_validate_json(record.model_dump_json())
            now = self.clock()
            if (
                record.envelope_sha256 != digest
                or record.policy_sha256 != self.trusted_policy_sha256
                or not record.reviewed_at <= now < record.expires_at
                or self.authority.permits(record) is not True
                or {a.binding.part_id: a.binding for a in record.assessments}
                != {p.part_id: p.binding() for p in parts}
            ):
                raise CompetitionDataFault("competition_data_unreviewed")
            if record.origin != "synthetic_from_scratch" or record.generator_sha256 is None:
                raise CompetitionDataFault("competition_data_provenance")
            for assessment in record.assessments:
                for category in assessment.categories:
                    if category.classification == "unknown":
                        raise CompetitionDataFault("competition_data_unreviewed")
                    if category.classification == "present" and not (
                        category.category == "financial_information"
                        and self.policy.synthetic_financial_clarification_sha256 is not None
                    ):
                        raise CompetitionDataFault("competition_data_prohibited")
            # Current authority may perform slow I/O. Its earlier time check
            # cannot authorize bytes once the exact review has expired.
            if not record.reviewed_at <= self.clock() < record.expires_at:
                raise CompetitionDataFault("competition_data_unreviewed")
        except CompetitionDataFault:
            raise
        except Exception:
            raise CompetitionDataFault("competition_data_unreviewed") from None


def privacy_export_parts(payload: PrivacyExportPayload) -> tuple[DataPart, ...]:
    """Every carrier member is bound, including absence of optional text.

    Reviewing the complete PDF means inspecting decoded streams, every raster
    page and embedded metadata, not just its native text layer. The page-image
    part binds the same complete PDF because embedded images are not separate
    transport objects. Any later extracted JSON, model envelope, log, mapping or
    restored output requires its own exact admission; this record cannot cover it.
    """
    return (
        DataPart("pdf", "pdf", payload.pdf),
        DataPart("embedded-pages", "page_image", payload.pdf),
        DataPart("manifest", "metadata", payload.manifest_json.encode()),
        DataPart("filename", "filename", payload.filename.encode()),
        DataPart("reviewer-text", "human_text", (payload.reviewer_text or "").encode()),
    )
