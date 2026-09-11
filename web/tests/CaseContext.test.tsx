/** Canonical-shape component inputs for unit regression only, not real-case results. */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CaseContextView } from "@/api/client";
import { CaseContext } from "@/features/CaseContext";
import { LanguageProvider } from "@/ui/Language";
import { view } from "./fixtures";

function context(): CaseContextView {
  const identity = {
    case_id: "case-1",
    district: "Unit district",
    zone: "Unit zone",
    land_use_category: "Unit use",
    effective_date: "2026-01-01",
    version: "conditions-v1",
  };
  const source: NonNullable<CaseContextView["rule_bundle"]>["sources"][number] = {
    document_id: "unit-general-rules",
    version: "source-v3",
    content_hash: "a".repeat(64),
    pages: [2, 9],
    entry_id: "unit-general-entry",
    entry_version: "entry-v2",
    role: "general_rules",
    use: "procedure",
    district: identity.district,
    zone: identity.zone,
    land_use_category: identity.land_use_category,
    scopes: ["regional"],
    effective_from: null,
    effective_to: null,
    review_status: "candidate",
    evidence: [
      {
        document_id: "unit-general-rules",
        version: "source-v3",
        content_hash: "a".repeat(64),
        page: 9,
        region_id: "unit-row",
        bbox: [0, 0, 10, 10],
        excerpt: "Unit source evidence",
      },
    ],
    unresolved: ["effective_period_not_verified"],
  };
  const districtSource = {
    ...source,
    document_id: "unit-district-basis",
    content_hash: "b".repeat(64),
    version: "basis-v7",
    entry_id: "unit-district-entry",
    role: "district_basis" as const,
    use: "factor_rules" as const,
    evidence: source.evidence.map((citation) => ({
      ...citation,
      document_id: "unit-district-basis",
      version: "basis-v7",
      content_hash: "b".repeat(64),
    })),
  };
  return {
    schema_version: "service-v1",
    identity,
    job: { schema_version: "service-v1", case_id: identity.case_id, job_id: "unit-job" },
    revision: view().task.run.revision,
    documents: [
      {
        schema_version: "service-v1",
        case_id: identity.case_id,
        document_id: source.document_id,
        version: source.version,
        content_hash: source.content_hash,
        purpose: "criteria",
      },
    ],
    observations: [],
    rules: [],
    selections: [],
    rule_bundle: {
      catalog_version: "catalog-v5",
      catalog_digest: "c".repeat(64),
      conditions_confirmed: false,
      identity,
      contexts: [{ scope: "regional", target_id: "unit-target", comparable_id: "unit-comparable" }],
      primary_criteria_document_id: districtSource.document_id,
      sources: [source, districtSource],
    },
  };
}

function show(value: CaseContextView) {
  return render(
    <LanguageProvider language="zh">
      <CaseContext context={value} />
    </LanguageProvider>,
  );
}

describe("CaseContext source summary (unit regression)", () => {
  it("shows actual role and document version independently for each selected source", () => {
    show(context());
    const summary = screen.getByLabelText("本次選取的規則來源");
    expect(within(summary).getByText("通用計算規則")).toBeVisible();
    expect(within(summary).getByText("地區地價基準")).toBeVisible();
    expect(within(summary).getByText(/source-v3/)).toBeVisible();
    expect(within(summary).getByText(/basis-v7/)).toBeVisible();
    expect(within(summary).getAllByText("候選資料，尚待核對")).toHaveLength(2);
    expect(screen.getByText(/案件條件尚未確認/)).toBeVisible();
    expect(screen.queryByText(/目前 criteria 用途尚未分出/)).not.toBeInTheDocument();
  });
  it("keeps catalog version, evidence pages, digests and unresolved metadata inspectable", () => {
    const { container } = show(context());
    container.querySelector("details")!.open = true;
    expect(screen.getByText("catalog-v5")).toBeVisible();
    const source = screen.getByRole("region", { name: "通用計算規則 unit-general-rules" });
    expect(within(source).getByText("unit-general-entry · entry-v2")).toBeVisible();
    expect(within(source).getByText("2, 9")).toBeVisible();
    expect(within(source).getByText("a".repeat(64))).toBeVisible();
    expect(within(source).getByText(/起日未提供/)).toBeVisible();
    expect(within(source).getByText("effective_period_not_verified")).toBeVisible();
    expect(screen.getByText(/目錄雜湊不等同組合識別碼/)).toBeVisible();
  });
  it.each([null, undefined])("does not invent source roles when bundle is %s", (bundle) => {
    const value = context();
    if (bundle === undefined) delete value.rule_bundle;
    else value.rule_bundle = bundle;
    const { container } = show(value);
    container.querySelector("details")!.open = true;
    expect(screen.queryByLabelText("本次選取的規則來源")).not.toBeInTheDocument();
    expect(screen.getByText(/此修訂未提供規則組合/)).toBeVisible();
    expect(screen.getByText(/未記錄獨立版本的報表模板/)).toBeVisible();
  });
  it("does not label reviewed catalog metadata or confirmed conditions as execution approval", () => {
    const value = context();
    const bundle = value.rule_bundle!;
    bundle.conditions_confirmed = true;
    bundle.sources = bundle.sources.map((source) => ({
      ...source,
      review_status: "reviewed",
      effective_from: "2025-01-01",
      effective_to: "2027-01-01",
      unresolved: [],
    }));
    show(value);
    expect(screen.getAllByText("目錄資料已核對")).toHaveLength(2);
    expect(screen.getByText(/規則執行與發布仍須另行核准/)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("適用規則或案件條件尚缺");
    expect(screen.queryByText("已標記核准")).not.toBeInTheDocument();
  });
});
