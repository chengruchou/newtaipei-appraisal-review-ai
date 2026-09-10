"""Conservative local format/label candidates; manual review remains mandatory."""

from __future__ import annotations

import re
import time
from uuid import uuid4

from appraisal_review.adapters.local.privacy.process import ScanFailure
from appraisal_review.domain.privacy_models import (
    PrivacyRegion,
    SensitiveCandidate,
    SensitiveCategory,
)
from appraisal_review.domain.privacy_scan import ScanIssue, TextObservation

_FORMAT_PATTERNS = (
    (
        SensitiveCategory.EMAIL,
        r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    ),
    (SensitiveCategory.IDENTITY_NUMBER, r"(?<![A-Za-z0-9])[A-Z][12][0-9]{8}(?![0-9])"),
    (SensitiveCategory.BUSINESS_NUMBER, r"(?<![0-9])[0-9]{8}(?![0-9])"),
    (SensitiveCategory.PHONE, r"(?<![0-9])0[0-9]{1,3}[ -]?[0-9]{3,4}[ -]?[0-9]{3,4}(?![0-9])"),
)
_LABELS = (
    (SensitiveCategory.NAME, r"姓名|所有權人|聯絡人|\bName\b|\bOwner\b"),
    (SensitiveCategory.ADDRESS, r"地址|住址|通訊處|\bAddress\b"),
    (SensitiveCategory.BIRTH_DATE, r"生日|出生日期|出生年月日|\bBirth\s*date\b"),
    (SensitiveCategory.ACCOUNT, r"帳號|帳戶|\bAccount\b"),
    (SensitiveCategory.IDENTITY_NUMBER, r"身分證字號|身份證字號|證件號碼|\bID\b"),
    (SensitiveCategory.CASE_CONTACT, r"案件聯絡資訊|\bContact\b"),
    (SensitiveCategory.PARCEL, r"地號|\bParcel\b"),
    (SensitiveCategory.OWNERSHIP_FINANCIAL, r"產權|持分|\bOwnership\b"),
)
_PATTERNS = tuple(
    (category, re.compile(pattern, re.IGNORECASE))
    for category, pattern in (
        *_FORMAT_PATTERNS,
        *(
            (category, rf"(?:{label})[ \t:\uFF1A]*\n?[ \t]*[^ \t:\uFF1A\r\n][^\r\n]{{0,119}}")
            for category, label in _LABELS
        ),
    )
)


def detect_candidates(
    observations: tuple[TextObservation, ...],
    *,
    max_candidates: int = 1000,
    deadline: float | None = None,
) -> tuple[SensitiveCandidate, ...]:
    """Match one page's evidence projection, retaining original observations separately.

    Newline joins are explicit projection separators, not inferred document text.
    Candidate boxes cover entire contributing blocks/words, conservatively.
    """
    if not observations:
        return ()
    if len({o.region.page for o in observations}) != 1:
        raise ValueError("Detection requires one page")
    if len(observations) > 10_000:
        raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
    text = "\n".join(o.text for o in observations)
    ranges = []
    cursor = 0
    for observation in observations:
        ranges.append((cursor, cursor + len(observation.text), observation))
        cursor += len(observation.text) + 1
    candidates: list[SensitiveCandidate] = []
    seen = set()
    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if deadline is not None and time.monotonic() >= deadline:
                raise ScanFailure(ScanIssue.TIMEOUT)
            contributing = [
                o for start, end, o in ranges if start < match.end() and end > match.start()
            ]
            boxes = [o.region.bbox for o in contributing]
            if not boxes:
                continue
            box = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            key = (category, match.group(), box)
            if key in seen:
                continue
            if len(candidates) >= max_candidates:
                raise ScanFailure(ScanIssue.RESOURCE_LIMIT)
            seen.add(key)
            scores = [o.confidence for o in contributing]
            confidence = (
                None if any(s is None for s in scores) else min(s for s in scores if s is not None)
            )
            candidates.append(
                SensitiveCandidate(
                    candidate_id=uuid4(),
                    category=category,
                    region=PrivacyRegion(page=observations[0].region.page, bbox=box),
                    raw_text=match.group(),
                    confidence=confidence,
                    detector_id="local-format-label",
                    detector_version="1",
                )
            )
    return tuple(candidates)
