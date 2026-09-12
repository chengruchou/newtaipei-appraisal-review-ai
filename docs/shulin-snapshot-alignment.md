# Shulin snapshot alignment (case data -> mapping source keys)

`scripts/build_shulin_calculation_snapshot.py` bridges the extracted Shulin
case artifacts (`artifacts/shulin-case/{extraction,criteria,computed}.json`)
to the official-table mapping vocabulary (`configs/mappings/table{3,4,5}-v1.json`)
and emits one validated `CalculationSnapshot`
(`artifacts/shulin-case/snapshot.json`). Every one of the 776 bound source
keys is either provided or an explained gap - never silently absent.
`tests/unit/test_shulin_snapshot_build.py` pins the honesty properties
(skipped where the gitignored artifacts are missing).

## Counts (run of 2026-09-12)

| Table | Bound keys | Present | Not applicable | Gaps |
|---|---|---|---|---|
| 表3 | 198 | 19 | 0 | 179 |
| 表4 | 218 | 47 | 0 | 171 |
| 表5 | 360 | 109 | 33 | 218 |
| Total | 776 | 175 (71 given_input, 104 computed) | 33 | 568 |

Writer proof (real `fill_workbook`, real templates, outputs under
`artifacts/shulin-case/filled/`): 表3 19 cells written / 179 skipped,
表4 47 / 171, 表5 109 / 251 (the 33 not-applicable rows render blank per the
mapping's absence policy). Every skip carries its gap reason.

## Alignment method

1. **Normalized zh-label matching** per subject segment: NFKC fold, fullwidth
   parentheses unified, parenthetical qualifiers dropped, subject prefixes
   (比較標的1/2/3, 比準地) and role suffixes (修正百分比/優劣等級/優劣註記/差異率)
   stripped. Used for all 表3 attribute cells (P001's zone sheet, per the
   mapping docs) and to **audit** every factor alias below - an alias whose
   labels disagree outside the approved-variant list is demoted to a gap.
   The audit reported zero mismatches.
2. **Hand-curated alias tables** in the script: `REGIONAL_STEM` (29
   computed.json regional factor ids -> `table_5` field stems),
   `INDIVIDUAL_STEM` (20 individual factor ids -> `table_4` stems), plus the
   structural fields (案號, 估價基準日 ROC string, 宗地流水號, 地價區段號 from
   區段編號, 表4/表5 備註 verbatim quotes, subtotals, price chain).
   `APPROVED_LABEL_VARIANTS` lists the nine wording variants (e.g. 土地改良 ↔
   建築基地改良…或其他改良, 環境污染 ↔ 水污染、噪音污染…) that were verified by eye.
3. **Percentage discipline**: every percentage is stored once, as points
   (Decimal string), with unit `percent_points` - the exact unit string the
   mappings declare (the writer enforces equality; the task brief's
   `percentage_points` spelling is the value_kind, not the unit). 表4's
   fraction rendering (5.96 -> 0.0596) is done by the mapping at write time;
   the snapshot never pre-divides.
4. Origins: extraction.json values are `given_input` with page + quote traces;
   computed.json values are `computed` carrying their arithmetic traces.
   Nothing is `human_confirmed`. Blank is never 0; no equal weights; no 範本
   figure appears (asserted by construction in the test).

## Unmatched keys (568 gaps, grouped)

- **224 - blank 表3 facility rows**: names/distances/grades for the 13
  facility factors (stations, schools, markets, parks, pollution, ...) are
  unmarked on every sheet; cannot distinguish "does not exist" from
  "not surveyed".
- **140 - blank parcel attributes**: 表4 rows 6-21 inputs and 差異率 (面積/寬度/
  深度/形狀/臨街情形/地勢/道路種類/面前道路寬度/proximities/嫌惡設施/停車方便性/無尾巷)
  for all four subjects.
- **138 - grade-code convention**: the sheets' numeric 優劣等級 code columns
  (and 表3 `_grade_scale`); the 優..劣 word-to-integer convention was not
  established from the assignment, so word labels (`_grade_label`) are
  provided and integer codes are not guessed.
- **24 - no confident alignment**: 表3 rows the assignment leaves unstated
  (soil/wind/industrial-water/customer-traffic/... and 勘查日期), per-subject
  備註 and 實例編號, and 表5 其他影響因素修正細項 (the pre-filled "－" vs "無"
  cell split is ambiguous).
- **16 - downstream of blockers**: 合計/絕對值加總/相近程度/試算價格/權重/
  比準地比較價格; the multi-comparable weight rule comes from the 作業手冊,
  never assumed equal.
- **12 - incomplete group subtotals**: 表5 groups (2)(5)(6)(7) have missing
  member factors; a partial subtotal must not pose as complete.
- **8 - awaiting human confirmation**: P001 zoning (第一種住宅區 field vs
  捷運開發區 range text; ±3.75 points per comparable on 表4 row 22) and the
  容積率 correction method for P002 (row 24 has no matrix; 土地開發分析法).
- **6 - partial regional totals**: 區域因素調整百分率 / 表5 總修正數 held back;
  the computed partial sums (P002 23.5 / P003 14.75 / P004 14.75 points over
  12 computable + 1 given factors) are NOT the totals.

## What remains for human confirmation

1. P001 zoning ruling (and whether 捷運開發區 maps to 捷運用地(聯開)).
2. 容積率 correction for P002 via 土地開發分析法 (or an organizer instruction).
3. Semantics of the blank 表3 facility rows (13 regional factors).
4. The missing parcel attributes for 表4 rows 6-21.
5. The numeric grade-code convention for the 優劣等級 code columns.

Until these land, the snapshot deliberately leaves the price pipeline
(區域/宗地 totals, 試算價格, weights, 比準地比較價格) blank.
