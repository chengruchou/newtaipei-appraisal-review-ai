"""The shipped mappings stay honest against the real organizer templates.

Skips cleanly when ``artifacts/official-templates`` is not materialized.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from appraisal_review.adapters.local.workbook_inventory import inventory_workbook
from appraisal_review.domain.official_table_mapping import TableMapping, validate_mapping

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "artifacts" / "official-templates"
MAPPINGS = ROOT / "configs" / "mappings"

pytestmark = pytest.mark.skipif(
    not TEMPLATES.is_dir(), reason="official templates are not materialized"
)

SOURCE_KEY = re.compile(r"^table_[345]\.(case|P00[1-4])\.[a-z][a-z0-9_]*$")

CASES = (
    ("table3-v1.json", "table_3", "表3"),
    ("table4-v1.json", "table_4", "表4"),
    ("table5-v1.json", "table_5", "表5"),
)


def _load(name: str) -> TableMapping:
    return TableMapping.model_validate(json.loads((MAPPINGS / name).read_text(encoding="utf-8")))


@pytest.mark.parametrize(("name", "table", "prefix"), CASES)
def test_mapping_is_pinned_to_the_template_bytes(name: str, table: str, prefix: str) -> None:
    mapping = _load(name)
    template = next(TEMPLATES.glob(f"{prefix}*.xlsx"))
    assert mapping.table == table
    assert mapping.template_digest == hashlib.sha256(template.read_bytes()).hexdigest()


@pytest.mark.parametrize(("name", "table", "prefix"), CASES)
def test_every_binding_is_writable_on_the_real_template(name: str, table: str, prefix: str) -> None:
    mapping = _load(name)
    inventory = inventory_workbook(next(TEMPLATES.glob(f"{prefix}*.xlsx")))
    assert validate_mapping(mapping, inventory) == ()


@pytest.mark.parametrize(("name", "table", "prefix"), CASES)
def test_source_keys_follow_the_documented_convention(name: str, table: str, prefix: str) -> None:
    mapping = _load(name)
    sources = [binding.source for binding in mapping.bindings]
    assert len(sources) == len(set(sources)), "one source key feeds one cell"
    for source in sources:
        assert SOURCE_KEY.match(source), source
        assert source.startswith(f"{table}."), source


def test_table4_carries_all_three_comparables_and_the_comparison_base() -> None:
    sources = {binding.source for binding in _load("table4-v1.json").bindings}
    for subject in ("P001", "P002", "P003", "P004"):
        assert any(source.startswith(f"table_4.{subject}.") for source in sources), subject
    assert "table_4.P001.compared_price" in sources
    for subject in ("P002", "P003", "P004"):
        assert f"table_4.{subject}.trial_price" in sources
        assert f"table_4.{subject}.total_adjustment_pct" in sources


def test_table5_binds_grade_percentage_and_subtotals_per_comparable() -> None:
    sources = {binding.source for binding in _load("table5-v1.json").bindings}
    for subject in ("P002", "P003", "P004"):
        assert f"table_5.{subject}.urban_plan_adjustment_pct" in sources
        assert f"table_5.{subject}.total_adjustment_pct" in sources
        for group in range(1, 9):
            assert f"table_5.{subject}.group{group}_subtotal_pct" in sources


def test_percent_kinds_respect_each_tables_convention() -> None:
    table4 = _load("table4-v1.json")
    assert all(binding.value_kind != "percentage_points" for binding in table4.bindings), (
        "表4 renders percentages as fractions"
    )
    table5 = _load("table5-v1.json")
    assert all(binding.value_kind != "percentage_fraction" for binding in table5.bindings), (
        "表5 keeps percentage points as points"
    )
