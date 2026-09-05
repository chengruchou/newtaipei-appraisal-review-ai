"""Conservative native-table candidate extraction; never grants rule approval."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import Field, ValidationError

from appraisal_review.domain.document_models import (
    DocumentModel,
    SourceCitation,
    SourceDocument,
    SourceRegion,
)
from appraisal_review.domain.extraction_models import ProposedRule
from appraisal_review.domain.factor_models import (
    CategoryBand,
    CorrectionMatrix,
    FactorRule,
    Grade,
    IntervalBand,
)

_LABELS = {
    "優": Grade.EXCELLENT,
    "稍優": Grade.SLIGHTLY_SUPERIOR,
    "普通": Grade.NORMAL,
    "稍劣": Grade.SLIGHTLY_INFERIOR,
    "劣": Grade.INFERIOR,
}
_UNITS = {"m": 1.0, "公尺": 1.0, "km": 1000.0, "公里": 1000.0, "cm": 0.01, "mm": 0.001}


class NativeCandidate(DocumentModel):
    title: str
    evidence: list[SourceCitation]
    candidate: ProposedRule | None = None
    unresolved: list[str] = Field(default_factory=list)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _bands(descriptions: dict[str, str]) -> tuple[list[IntervalBand], list[CategoryBand]]:
    intervals, categories = [], []
    numeric = any(re.search(r"\d.*(?:m|公尺|公里)", text) for text in descriptions.values())
    for label, description in descriptions.items():
        grade = _LABELS[label]
        if not numeric:
            categories.append(CategoryBand(grade=grade, values=[description]))
            continue
        if description == "區段內有":
            categories.append(CategoryBand(grade=grade, values=["within_section"]))
            continue
        if "或無" in description or "及無" in description:
            categories.append(CategoryBand(grade=grade, values=["absent"]))
        tokens = re.findall(r"([\d,]+(?:\.\d+)?)(km|cm|mm|m|公尺|公里)", description)
        if not tokens:
            raise ValueError("Unresolved presence or numeric semantics")
        numbers = [float(number.replace(",", "")) * _UNITS[unit] for number, unit in tokens]
        if len(tokens) == 2 and "以上未滿" in description:
            intervals.append(IntervalBand(grade=grade, minimum=numbers[0], maximum=numbers[1]))
        elif len(tokens) == 1 and description.startswith("未滿"):
            intervals.append(IntervalBand(grade=grade, maximum=numbers[0]))
        elif len(tokens) == 1 and "以上" in description:
            intervals.append(IntervalBand(grade=grade, minimum=numbers[0]))
        else:
            raise ValueError("Unsupported endpoint wording")
    intervals.sort(key=lambda band: float("-inf") if band.minimum is None else band.minimum)
    return intervals, categories


def native_candidates(source: SourceDocument) -> list[NativeCandidate]:
    """Find grade-row matrices by labels and geometry, not document filenames."""
    output = []
    for page in source.pages:
        heading = _compact(" ".join(r.text for r in page.regions if r.kind == "text"))
        scope: Literal["regional", "individual"] | None = (
            "regional" if "區域因素" in heading else "individual" if "個別因素" in heading else None
        )
        cells = [r for r in page.regions if r.kind == "cell"]
        coordinates = {(r.table_id, r.row, r.column): r for r in cells}
        for start in cells:
            if _compact(start.text) != "優" or start.row is None or start.column is None:
                continue
            right = coordinates.get((start.table_id, start.row, start.column + 1))
            if right is None or _compact(right.text) not in {"0", "0.0", "0.00"}:
                continue
            refs: list[SourceRegion] = [start]
            label_cells = [
                r
                for r in cells
                if r.table_id == start.table_id
                and r.column is not None
                and r.column < start.column
                and r.bbox[1] <= start.bbox[1] <= r.bbox[3]
                and _compact(r.text) not in {"", "價格修正率", "主要項目", "細項"}
            ]
            title = (
                _compact(max(label_cells, key=lambda r: r.bbox[0]).text)
                if label_cells
                else "unlocated-title"
            )
            record = NativeCandidate(title=title, evidence=[])
            try:
                if scope is None or title == "unlocated-title":
                    raise ValueError("Scope or factor label needs review")
                for offset, label in enumerate(_LABELS):
                    header = coordinates[(start.table_id, start.row - 1, start.column + 1 + offset)]
                    if _compact(header.text) != label:
                        raise ValueError("Matrix column order is not proven by headers")
                    refs.append(header)
                matrix: dict[str, dict[str, float]] = {}
                descriptions = []
                for offset, (label, grade) in enumerate(_LABELS.items()):
                    row = start.row + offset
                    marker = coordinates[(start.table_id, row, start.column)]
                    if _compact(marker.text) != label:
                        raise ValueError("Unsupported or misaligned matrix grade rows")
                    values = []
                    for column in range(start.column + 1, start.column + 6):
                        region = coordinates[(start.table_id, row, column)]
                        values.append(float(_compact(region.text)))
                        refs.append(region)
                    matrix[grade.value] = {
                        g.value: v for g, v in zip(_LABELS.values(), values, strict=True)
                    }
                    for region in cells:
                        if (
                            region.table_id == start.table_id
                            and region.row == row
                            and region.column is not None
                            and region.column > start.column + 5
                        ):
                            if region.text not in descriptions:
                                descriptions.append(region.text)
                            refs.append(region)
                parts = re.split(
                    r"(稍優|稍劣|普通|優|劣)[:\uFF1A]", _compact("\n".join(descriptions))
                )
                description_map = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
                if set(description_map) != set(_LABELS):
                    raise ValueError("Incomplete classification descriptions")
                intervals, categories = _bands(description_map)
                factor_id = f"{scope}.factor.{hashlib.sha256(title.encode()).hexdigest()[:12]}"
                rule = FactorRule(
                    id=f"{factor_id}.{source.version}",
                    factor_id=factor_id,
                    kind="presence_distance"
                    if intervals and categories
                    else "numeric_interval"
                    if intervals
                    else "category",
                    unit="m" if intervals else None,
                    intervals=intervals,
                    categories=categories,
                    correction_matrix=CorrectionMatrix(values=matrix),
                )
                record.candidate = ProposedRule(
                    scope=scope,
                    rule=rule,
                    evidence=[
                        SourceCitation(
                            document_id=source.document_id,
                            content_hash=source.content_hash,
                            version=source.version,
                            page=page.number,
                            region_id=start.id,
                            bbox=start.bbox,
                            excerpt=start.text,
                        )
                    ],
                )
            except (ValueError, KeyError, ValidationError):
                record.unresolved.append(
                    "Matrix, units, classification shape or scope needs human interpretation"
                )
            record.evidence = [
                SourceCitation(
                    document_id=source.document_id,
                    content_hash=source.content_hash,
                    version=source.version,
                    page=page.number,
                    region_id=r.id,
                    bbox=r.bbox,
                    excerpt=r.text,
                )
                for r in refs
            ]
            if record.candidate is not None:
                record.candidate.evidence = record.evidence
            output.append(record)
    return output
