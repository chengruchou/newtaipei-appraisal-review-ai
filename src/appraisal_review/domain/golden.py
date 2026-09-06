"""Explicit local golden comparisons with separate measured denominators."""

from pydantic import Field

from appraisal_review.domain.document_models import Digest, DocumentModel, SourceRegistry


class GoldenField(DocumentModel):
    name: str
    document_id: str
    content_hash: Digest
    page: int = Field(ge=1)
    region_id: str
    expected_text: str
    expected_selection: str | None = None


class GoldenSet(DocumentModel):
    description: str
    fields: list[GoldenField]


def check_fields(registry: SourceRegistry, golden: GoldenSet) -> dict[str, object]:
    rows = []
    for field in golden.fields:
        sources = [
            d
            for d in registry.documents
            if d.document_id == field.document_id and d.content_hash == field.content_hash
        ]
        region = None
        if len(sources) == 1 and field.page <= len(sources[0].pages):
            region = next(
                (r for r in sources[0].pages[field.page - 1].regions if r.id == field.region_id),
                None,
            )
        rows.append(
            {
                "name": field.name,
                "text_match": region is not None and region.text.strip() == field.expected_text,
                "selection_match": (
                    region is not None and region.selection == field.expected_selection
                )
                if field.expected_selection is not None
                else None,
            }
        )
    selected = [row for row in rows if row["selection_match"] is not None]
    return {
        "field_exact_match": {
            "correct": sum(bool(r["text_match"]) for r in rows),
            "total": len(rows),
        },
        "selection_exact_match": {
            "correct": sum(bool(r["selection_match"]) for r in selected),
            "total": len(selected),
        },
        "source_geometry_accuracy": "not measured by string matching",
        "rows": rows,
    }
