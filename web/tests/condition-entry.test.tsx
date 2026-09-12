/** Canonical-shape component inputs for unit regression only, not real-case results. */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { CaseContextView, TaskListView } from "@/api/client";
import { ConditionEntry } from "@/features/ConditionEntry";
import { LanguageProvider } from "@/ui/Language";
import { citation, view } from "./fixtures";

const identity = {
  case_id: "case-1",
  district: "Unit district",
  zone: "Unit section",
  land_use_category: "Unit use",
  effective_date: "2026-01-01",
  version: "conditions-v1",
};

function context(): CaseContextView {
  return {
    schema_version: "service-v1",
    identity,
    job: { schema_version: "service-v1", case_id: identity.case_id, job_id: "unit-job" },
    revision: view().task.run.revision,
    documents: [],
    observations: [],
    rules: [],
    selections: [],
    rule_bundle: {
      catalog_version: "catalog-v5",
      catalog_digest: "c".repeat(64),
      conditions_confirmed: false,
      identity,
      contexts: [{ scope: "regional", target_id: "T", comparable_id: "C1" }],
      primary_criteria_document_id: "unit-district-basis",
      sources: [],
      condition_candidates: [
        {
          field: "regulatory_zone",
          value: "Unit residential zone",
          interpretation: "Assessed on the pre-conversion land specification.",
          method: "manual_proposed",
          evidence: [],
        },
        {
          field: "district",
          value: "Unit district",
          interpretation: "Parsed from the case header.",
          method: "native_proposed",
          evidence: [citation],
        },
      ],
    },
  } as unknown as CaseContextView;
}

function tasks(): TaskListView {
  return {
    schema_version: "service-v1",
    job: { schema_version: "service-v1", case_id: identity.case_id, job_id: "unit-job" },
    tasks: [
      view({
        task_id: "task-district",
        kind: "material_correction",
        required_permission: "correct_material",
        allowed_responses: ["correct", "reject"],
        affected_subject_ids: ["conditions:district"],
      }),
    ],
  } as unknown as TaskListView;
}

function show(value: CaseContextView = context(), list: TaskListView = tasks()) {
  return render(
    <LanguageProvider language="zh">
      <MemoryRouter>
        <ConditionEntry context={value} tasks={list} jobId="unit-job" />
      </MemoryRouter>
    </LanguageProvider>,
  );
}

const rowFor = (text: string) =>
  within(screen.getByLabelText("案件條件"))
    .getAllByRole("listitem")
    .find((item) => within(item).queryByText(new RegExp(text)) !== null)!;

describe("ConditionEntry (unit regression)", () => {
  it("shows the current district, use and effective date from the service identity", () => {
    show();
    const panel = screen.getByLabelText("案件條件");
    expect(within(panel).getByText("Unit district")).toBeVisible();
    expect(within(panel).getByText("Unit use")).toBeVisible();
    expect(within(panel).getByText("2026-01-01")).toBeVisible();
    expect(within(panel).getByText("conditions-v1")).toBeVisible();
  });
  it("displays a candidate's origin and never as a confirmed condition", () => {
    show();
    const manual = rowFor("法定使用分區");
    expect(within(manual).getByText("人工解讀候選")).toBeVisible();
    expect(within(manual).getByText(/並非從官方來源解析而得/)).toBeVisible();
    const native = rowFor("行政區：Unit district");
    expect(within(native).getByText("原生解析候選")).toBeVisible();
    expect(within(native).queryByText(/並非從官方來源解析而得/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("以下皆為候選解讀，尚未套用");
    expect(screen.queryByText("已標記核准")).not.toBeInTheDocument();
  });
  it("links to the canonical correction task and says so where none exists", () => {
    show();
    expect(
      within(rowFor("行政區：Unit district")).getByRole("link", { name: /開啟更正任務/ }),
    ).toHaveAttribute("href", "/jobs/unit-job/tasks/task-district");
    const manual = rowFor("法定使用分區");
    expect(within(manual).queryByRole("link")).not.toBeInTheDocument();
    expect(within(manual).getByText(/服務未提供此條件的待處理更正任務/)).toBeVisible();
    expect(within(manual).getByText("服務未提供原文依據。")).toBeVisible();
  });
  it("reads a district without a supplied rule bundle as unsupported", () => {
    const value = context();
    delete value.rule_bundle;
    show(value);
    expect(screen.getByRole("status")).toHaveTextContent("本部署視此行政區為未支援");
    expect(screen.getByRole("status")).toHaveTextContent("不會改用其他行政區的規則");
    expect(screen.getByText(/此處不會自行合成條件/)).toBeVisible();
  });
});
