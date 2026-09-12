# Official workbook writer and cell mappings

## How the mappings were authored

Each visible sheet's populated cells and merged ranges were dumped with
`scripts/dump_workbook_cells.py` / `workbook_inventory` from the real templates
under `artifacts/official-templates/` (gitignored; never committed). Cell
meanings were cross-referenced against the organizer's pre-filled sample
`查估書表範本.pdf`: page 1 for 表3 (區段勘查表 layout), page 2 for 表5 (the
表5-2 variant shares the 表5-1 column layout), page 3 for 表4. The sample was
used only to identify which cell holds which quantity; none of its numeric
answers appear in the mappings, defaults or tests. Every binding addresses a
merge anchor (or plain cell) and each mapping was checked with
`validate_mapping` against the template inventory: zero findings
(`tests/unit/test_official_mappings.py` re-asserts this).

`template_digest` pins each mapping to the exact template bytes
(表3 `f85bc407…`, 表4 `c5f767e8…`, 表5 `d2ab6632…`); the writer refuses any
other bytes.

## Source-key convention

`table_<n>.<scope>.<field>` where scope is `case` or a subject id: `P001`
(比準地, comparison base), `P002`/`P003`/`P004` (比較標的 1/2/3, in column
order). Fields are snake_case English; the Chinese wording lives in each
binding's `label`. Percentages are stored once, as points with unit
`percent_points`; 表5 bindings render points as points
(`percentage_points`, 5 → `5.00`) and 表4 bindings render the fraction form
(`percentage_fraction`, 5 → `0.0500`). Distances use unit `m`, areas `m2`,
prices `TWD_per_m2`. Grades are integers; `_grade_scale` is the number of
grade levels the factor uses (2 for 有無-type factors, else 5 - inferred from
the sample's paired code columns, e.g. 都市計畫 `1|2`, 建蔽率 `1|5`).

- `configs/mappings/table3-v1.json` - 198 bindings, all `table_3.case.*`
  (the form describes the comparison base's one 地價區段).
- `configs/mappings/table4-v1.json` - 218 bindings: case header
  (`valuation_date`, `case_number`, `note`), P001 conditions and
  `compared_price`, and per comparable the price chain
  (`normal_unit_price` → `date_adjustment_pct` → `adjusted_unit_price` →
  `region_adjustment_pct` → factor rows 7-25 with `_adjustment_pct` →
  `total_adjustment_pct`, `abs_adjustment_sum_pct`, `similarity`,
  `trial_price`, `weight_pct`, `note`).
- `configs/mappings/table5-v1.json` - 360 bindings: per factor row a
  `_grade` code and `_grade_label` word for every subject plus
  `_adjustment_pct` per comparable, `group1..8_subtotal_pct`,
  `total_adjustment_pct`, zone numbers, sample numbers and notes.

## Unmapped or ambiguous cells (deliberately left out)

- 表3 selection circles (○/●) and checkbox rows: the marks live inside
  printed label cells (e.g. `H13` "高鐵站 ○本區段內…", `E31:K34` 土地改良
  checkboxes, `Q44` 土地利用現況, `G18` 密集程度) - writing them would
  replace the organizer's printed wording, so selection state is not
  mappable cell-wise.
- 表3/表4 signature and date lines that share one printed cell
  (表4 `A35` "填寫日期：… 承辦員：…"; 表3 `H45`/`L45`/`R45`/`R46`): human
  signature areas, and the value would overwrite the label.
- 表4 `D5:F7` (比準地 土地正常單價/交易日期/調整後單價): the sample leaves
  the comparison base's transaction chain blank; meaning of a filled value
  is undefined.
- 表4 road-type distance subcells (`E15`-column analogues) and parking
  distance subcells `E23/H23/L23/P23`: the template prints an `M` unit there
  but the sample never fills them.
- 表3 `H6`/`H7` (建蔽率/容積率) are bound as display text
  (`building_coverage_text`, e.g. "70%"): the template pre-prints a text
  dash there, so `not_applicable` renders the form's own `-`.

## Fidelity guarantees (enforced inside `fill_workbook`, pinned by tests)

The writer rewrites only the visible sheet's worksheet part inside the
original zip; every other part - 23 hidden legacy sheets, external-link
parts, styles, shared strings - is copied byte-identically and re-verified
after the write. Values are re-read from the produced bytes (XML re-parse
plus an openpyxl second opinion) and must match exactly. Numbers are written
as exact decimal text (no float round-trip), text as inline strings so a
leading `=` can never become a formula, and absence is never zero: missing
renders blank and is reported in `skipped` with its gap reason.
