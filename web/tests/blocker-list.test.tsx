/** Canonical-shape component inputs for unit regression only, not real-case results. */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { CaseContextView, PausedReviewView, TaskListView } from "@/api/client";
import { BlockerList, type BlockerInput } from "@/features/BlockerList";
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

function context(): CaseContextView {
  return {
    schema_version: "service-v1",
    identity,
    job: { schema_version: "service-v1", case_id: identity.case_id, job_id: "unit-job" },
    revision,
    documents: [],
    observations: [],
    rules: [
      {
        schema_version: "service-v1",
        reference: {
          schema_version: "service-v1",
          rule_set_id: "unit-rules",
          version: "v2",
          content_hash: "d".repeat(64),
          context: { scope: "regional", target_id: "T", comparable_id: "C1" },
        },
        applicability: {
          jurisdiction: identity.district,
          land_use_category: identity.land_use_category,
          effective_from: null,
          effective_to: null,
        },
        zone: identity.zone,
        declared_status: "candidate",
        evidence: [citation],
      },
    ],
    selections: [
      {
        schema_version: "service-v1",
        context: { scope: "regional", target_id: "T", comparable_id: "C1" },
        status: "ambiguous",
        matches: [],
      },
    ],
    rule_bundle: {
      catalog_version: "catalog-v5",
      catalog_digest: "c".repeat(64),
      conditions_confirmed: false,
      identity,
      contexts: [{ scope: "regional", target_id: "T", comparable_id: "C1" }],
      primary_criteria_document_id: "unit-district-basis",
      sources: [
        {
          document_id: "unit-district-basis",
          version: "basis-v7",
          content_hash: "b".repeat(64),
          pages: [2],
          entry_id: "unit-entry",
          entry_version: "entry-v2",
          role: "district_basis",
          use: "factor_rules",
          district: identity.district,
          zone: identity.zone,
          land_use_category: identity.land_use_category,
          scopes: ["regional"],
          effective_from: null,
          effective_to: null,
          review_status: "candidate",
          evidence: [citation],
          unresolved: ["effective_period_not_verified"],
        },
      ],
    },
  } as unknown as CaseContextView;
}

function assessment(overrides: Partial<PausedReviewView["coverage"]> = {}): PausedReviewView {
  return {
    schema_version: "service-v1",
    scope: "paused_review",
    status: "needs_review",
    run: view().task.run,
    findings: [],
    coverage: {
      required: ["road_width", "frontage", "shape"],
      verified: ["shape"],
      missing: ["road_width", "frontage"],
      unsupported: ["slope"],
      ...overrides,
    },
    verification: {
      schema_version: "service-v1",
      status: "needs_review",
      critical_errors: [
        {
          schema_version: "service-v1",
          code: "verification_blocker",
          message: "Verification could not pass; inspect review findings or request human review.",
        },
      ],
      warnings: [],
    },
  } as unknown as PausedReviewView;
}

function tasks(): TaskListView {
  return {
    schema_version: "service-v1",
    job: { schema_version: "service-v1", case_id: identity.case_id, job_id: "unit-job" },
    tasks: [view({ task_id: "task-road-width", finding_ids: ["road_width"] })],
  } as unknown as TaskListView;
}

function show(overrides: Partial<BlockerInput> = {}) {
  const input: BlockerInput = {
    context: context(),
    tasks: tasks(),
    assessment: assessment(),
    result: null,
    assessmentError: false,
    ...overrides,
  };
  return render(
    <LanguageProvider language="zh">
      <MemoryRouter>
        <BlockerList input={input} jobId="unit-job" />
      </MemoryRouter>
    </LanguageProvider>,
  );
}

const rows = () => within(screen.getByLabelText("未決項目")).getAllByRole("listitem");
const row = (heading: string) => rows().find((item) => within(item).queryByText(heading) !== null)!;

describe("BlockerList (unit regression)", () => {
  it("collapses rows of the same kind into one entry with a count", () => {
    show();
    const missing = row("必要檢核未覆蓋");
    expect(within(missing).getByText("2")).toBeVisible();
    expect(within(missing).getByText(/road_width、frontage/)).toBeVisible();
    expect(rows().filter((item) => within(item).queryByText("必要檢核未覆蓋"))).toHaveLength(1);
  });
  it("states a field, a reason, a source citation and a next action for every row", () => {
    show();
    const unresolved = row("來源目錄資料未解決");
    expect(within(unresolved).getByText("影響對象：unit-district-basis")).toBeVisible();
    expect(within(unresolved).getByText(/此來源的目錄資料尚未解決/)).toBeVisible();
    expect(within(unresolved).getByText(/forms.pdf v1 · 第 3 頁 · row-7/)).toBeVisible();
    const unsupported = row("必要檢核未支援");
    expect(within(unsupported).getByText(/來源引用：服務未提供/)).toBeVisible();
    expect(within(unsupported).getByText(/規則範圍須由管理者確認/)).toBeVisible();
  });
  it("links a coverage gap to the task the service actually opened for it", () => {
    show();
    const missing = row("必要檢核未覆蓋");
    expect(within(missing).getByRole("link", { name: /road_width/ })).toHaveAttribute(
      "href",
      "/jobs/unit-job/tasks/task-road-width",
    );
    expect(within(missing).queryByRole("link", { name: /frontage/ })).not.toBeInTheDocument();
  });
  it("keeps raw service codes available without putting them in the summary line", () => {
    show();
    const selection = row("適用規則版本衝突");
    const details = within(selection).getByRole("group");
    expect(within(details).getByText(/selection.ambiguous/)).toBeInTheDocument();
    expect(within(details).getByText(/無對應待處理任務/)).toBeInTheDocument();
    expect(within(row("規則尚未核准")).getByText(/declared_status:candidate/)).toBeInTheDocument();
  });
  it("distinguishes an unreadable assessment from an empty one", () => {
    const { unmount } = show({
      assessment: null,
      assessmentError: true,
      context: {
        ...context(),
        rules: [],
        selections: [],
        rule_bundle: { ...context().rule_bundle!, sources: [] },
      },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("這不代表沒有問題");
    expect(screen.queryByText(/本修訂在已發布欄位中未回報未決項目/)).not.toBeInTheDocument();
    unmount();
    show({
      assessment: assessment({ missing: [], unsupported: [] }),
      assessmentError: false,
      context: {
        ...context(),
        rules: [],
        selections: [],
        rule_bundle: { ...context().rule_bundle!, sources: [] },
      },
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("獨立驗證阻擋")).toBeVisible();
  });
  it("never reads as zero blockers when no assessment or result was supplied", () => {
    show({
      assessment: null,
      assessmentError: false,
      context: {
        ...context(),
        rules: [],
        selections: [],
        rule_bundle: { ...context().rule_bundle!, sources: [] },
      },
    });
    expect(screen.getByText(/這不等於零阻擋/)).toBeVisible();
  });
});
