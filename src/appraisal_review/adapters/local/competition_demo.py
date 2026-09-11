"""Invented arithmetic example with no case input, source document or cloud transport."""

from decimal import Decimal


def synthetic_financial_example() -> dict[str, object]:
    """All values are selected here; none renames or scales real appraisal material."""
    area = Decimal("120")
    unit_price = Decimal("1000")
    corrections = (Decimal("5"), Decimal("-3"), Decimal("7"))
    base = area * unit_price
    total = sum(corrections, Decimal(0))
    adjusted = (base * (Decimal(1) + total / 100)).quantize(Decimal("0.01"))
    return {
        "schema_version": "synthetic-financial-example-v1",
        "origin": "synthetic_from_scratch",
        "generator": "appraisal_review.adapters.local.competition_demo",
        "data_use": "Invented local demonstration; no real or transformed case input.",
        "area_square_meters": str(area),
        "unit_price_synthetic_currency": str(unit_price),
        "base_amount": str(base),
        "corrections_percent_points": [str(value) for value in corrections],
        "total_percent_points": str(total),
        "adjusted_amount": str(adjusted),
        "classification": "financial_information",
        "cloud_admission": "unapproved",
        "organizer_synthetic_financial_permission": "unknown",
    }
