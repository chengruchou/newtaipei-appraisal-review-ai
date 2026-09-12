/** Canonical-shape component inputs for unit regression only, not real-case results. */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { CaseContextView, TaskListView } from "@/api/client";
import { SubjectRoster } from "@/features/SubjectRoster";
import { LanguageProvider } from "@/ui/Language";
import { citation, view } from "./fixtures";

const revision = view().task.run.revision;
const identity = {
  case_id: "case-1",
  district: "Unit district",
  zone: "Unit section",
  land_use_category: "Unit use",
  effective_date: "2026-01-01",
  version: "conditions-v1",
};

function observation(side: "target" | "comparable", comparable: string) {
  return {
    schema_version: "service-v1" as const,
    side: {
      schema_version: "service-v1" as const,
      context: { scope: "regional" as const, target_id: "T", comparable_id: comparable },
      factor_id: "road_width",
      side,
      input_digest: "a".repeat(64),
    },
    observation: {
      schema_version: "service-v1" as const,
      state: "present" as const,
      value: "10",
      raw_text: "10 m",
      unit: "m",
      confidence: 0,
      evidence: [citation],
    },
  };
}

function context(overrides: Partial<CaseContextView> = {}): CaseContextView {
  return {
    schema_version: "service-v1",
    identity,
    job: { schema_version: "service-v1", case_id: identity.case_id, job_id: "unit-job" },
    revision,
    documents: [],
    // The comparable observation is listed first on purpose: role must come from the
    // service's identifiers, never from the order values happen to arrive in.
    observations: [observation("comparable", "C1"), observation("target", "C1")],
    rules: [],
    selections: [],
    rule_bundle: {
      catalog_version: "catalog-v5",
      catalog_digest: "c".repeat(64),
      conditions_confirmed: false,
      identity,
      contexts: [
        { scope: "regional", target_id: "T", comparable_id: "C1" },
        { scope: "regional", target_id: "T", comparable_id: "C2" },
        { scope: "individual", target_id: "T", comparable_id: "C3" },
      ],
      primary_criteria_document_id: "unit-district-basis",
      sources: [],
    },
    ...overrides,
  } as unknown as CaseContextView;
}

function tasks(): TaskListView {
  const open = view({
    task_id: "open-c1",
    side: {
      schema_version: "service-v1",
      context: { scope: "regional", target_id: "T", comparable_id: "C1" },
      factor_id: "road_width",
      side: "comparable",
      input_digest: "a".repeat(64),
    },
  });
  const answered = view({ task_id: "answered-target", state: "answered" });
  answered.task.side!.context = { scope: "regional", target_id: "T", comparable_id: "C1" };
  return {
    schema_version: "service-v1",
    job: { schema_version: "service-v1", case_id: identity.case_id, job_id: "unit-job" },
    tasks: [open, answered],
  } as unknown as TaskListView;
}

function show(value: CaseContextView = context(), list: TaskListView = tasks()) {
  return render(
    <LanguageProvider language="zh">
      <MemoryRouter>
        <SubjectRoster context={value} tasks={list} jobId="unit-job" />
      </MemoryRouter>
    </LanguageProvider>,
  );
}

describe("SubjectRoster (unit regression)", () => {
  it("reads one target and each comparable from the service's comparison contexts", () => {
    show();
    const roster = screen.getByLabelText("標的清單");
    const rows = within(roster).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]!).getByText("比準地")).toBeVisible();
    expect(within(rows[0]!).getByText("T")).toBeVisible();
    expect(rows.slice(1).map((row) => within(row).getByText("比較標的"))).toHaveLength(3);
    expect(within(rows[1]!).getByText("C1")).toBeVisible();
    expect(within(rows[3]!).getByText("C3")).toBeVisible();
  });
  it("reports a subject with no supplied observation as not supplied, never as zero", () => {
    show();
    const rows = within(screen.getByLabelText("標的清單")).getAllByRole("listitem");
    expect(within(rows[2]!).getByText(/已提供觀察值：尚未提供/)).toBeVisible();
    expect(within(rows[2]!).getByText(/引用來源頁碼：服務未提供/)).toBeVisible();
    expect(within(rows[2]!).queryByText(/已提供觀察值：0/)).not.toBeInTheDocument();
  });
  it("links only to a task that is actually open for that subject", () => {
    show();
    const rows = within(screen.getByLabelText("標的清單")).getAllByRole("listitem");
    expect(within(rows[1]!).getByRole("link", { name: /開啟目前任務/ })).toHaveAttribute(
      "href",
      "/jobs/unit-job/tasks/open-c1",
    );
    expect(within(rows[0]!).queryByRole("link")).not.toBeInTheDocument();
    expect(within(rows[0]!).getByText("此標的目前沒有待處理任務")).toBeVisible();
    expect(within(rows[0]!).getByText(/已記錄回覆：1/)).toBeVisible();
  });
  it("shows the cited source pages the service supplied for a subject", () => {
    show();
    const rows = within(screen.getByLabelText("標的清單")).getAllByRole("listitem");
    expect(within(rows[0]!).getByText(/引用來源頁碼：forms.pdf · 3/)).toBeVisible();
  });
  it("invents no roster when the revision published no comparison context", () => {
    const value = context();
    value.rule_bundle!.contexts = [];
    show(value);
    expect(screen.getByText(/此處不會自行補造標的/)).toBeVisible();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });
});
