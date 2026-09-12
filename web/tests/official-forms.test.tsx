/** Canonical-shape component inputs for unit regression only, not real-case results. */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CaseContextView } from "@/api/client";
import { OfficialForms } from "@/features/OfficialForms";
import { LanguageProvider } from "@/ui/Language";
import { view } from "./fixtures";

function context(zone = "Unit section"): CaseContextView {
  return {
    schema_version: "service-v1",
    identity: {
      case_id: "case-1",
      district: "Unit district",
      zone,
      land_use_category: "Unit use",
      effective_date: "2026-01-01",
      version: "conditions-v1",
    },
    job: { schema_version: "service-v1", case_id: "case-1", job_id: "unit-job" },
    revision: view().task.run.revision,
    documents: [],
    observations: [],
    rules: [],
    selections: [],
  } as unknown as CaseContextView;
}

function show(value: CaseContextView = context()) {
  return render(
    <LanguageProvider language="zh">
      <OfficialForms context={value} />
    </LanguageProvider>,
  );
}

describe("OfficialForms shell (unit regression)", () => {
  it("labels each form with its official name and visible worksheet", () => {
    show();
    expect(screen.getByRole("region", { name: "表 3 · 地價區段勘查表" })).toBeVisible();
    expect(screen.getByRole("region", { name: "表 4 · 比較法調查估價表" })).toBeVisible();
    expect(screen.getByText("表3區段勘查表")).toBeVisible();
    expect(screen.getByText("表4比較法調查估價表")).toBeVisible();
    expect(screen.getByText("表5-1區域因素明細表(住)")).toBeVisible();
  });
  it("keeps the land-use qualifier in the label for the form that carries one", () => {
    show();
    const form5 = screen.getByRole("region", {
      name: "表 5-1 · 影響地價區域因素分析明細表（住宅用地）",
    });
    expect(within(form5).getByText(/此表另有其他用途別版本/)).toBeVisible();
  });
  it("states each form's rate convention without converting between them", () => {
    show();
    expect(screen.getByText(/五個百分點儲存為 0.05，顯示為 5.00%/)).toBeVisible();
    expect(screen.getByText(/五個百分點儲存為 5，而不是 0.05/)).toBeVisible();
    expect(screen.getByText(/不在兩種比率慣例間換算/)).toBeVisible();
  });
  it("holds Form 3 as a list of section instances, not one fixed file", () => {
    show();
    const form3 = screen.getByRole("region", { name: "表 3 · 地價區段勘查表" });
    expect(within(form3).getByText(/本案已發布區段：Unit section \(1 份已知\)/)).toBeVisible();
    expect(within(form3).getByText(/所需份數不只一份/)).toBeVisible();
  });
  it("says a section is not supplied rather than assuming one", () => {
    show(context(""));
    const form3 = screen.getByRole("region", { name: "表 3 · 地價區段勘查表" });
    expect(within(form3).getByText(/本案已發布區段：服務未提供 \(0 份已知\)/)).toBeVisible();
  });
  it("offers no download and claims no draft, ready or completed file", () => {
    show();
    expect(screen.getAllByText(/本部署尚未提供：服務未發布此表的下載路由/)).toHaveLength(3);
    expect(screen.getByRole("alert")).toHaveTextContent("尚未產出任何官方表格");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/已就緒|已可下載|已產出|下載已驗證/)).not.toBeInTheDocument();
  });
});
