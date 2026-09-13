/**
 * Mocked component unit regression for the productized main copy: readable zh labels in
 * the primary flow, raw identifiers and mechanism prose kept verbatim but folded into
 * 技術紀錄／技術說明／版本證據 disclosures. No backend acceptance is implied.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReviewClient } from "@/api/client";
import { ApprovalPanel } from "@/features/ApprovalPanel";
import { ExportPanel } from "@/features/ExportPanel";
import { TaskPage } from "@/features/TaskPage";
import { LanguageProvider } from "@/ui/Language";
import { basis, serverBasis } from "./export-fixtures";
import { subjectView, view } from "./fixtures";

function zh(children: React.ReactNode) {
  return <LanguageProvider language="zh">{children}</LanguageProvider>;
}

describe("TaskPage main copy", () => {
  function client(): ReviewClient {
    return {
      readTask: vi.fn(() => Promise.resolve(view())),
      readTaskSubject: vi.fn(() => Promise.resolve(subjectView(view()))),
      readSource: vi.fn(() => Promise.reject(new Error("not read in this test"))),
    } as unknown as ReviewClient;
  }

  it("speaks in case, subjects and field words instead of raw identifiers", async () => {
    render(zh(<TaskPage taskId={view().task.task_id} client={client()} />));
    await screen.findByText(/標的 板橋-A × 三重-B/);
    expect(screen.getByText(/待確認欄位 道路寬度（基準側）/)).toBeVisible();
    // The question and its evidence stay untouched.
    expect(screen.getByText("Is the target road width 10 m?")).toBeVisible();
  });

  it("keeps case, revision and finding identifiers verbatim behind 技術紀錄", async () => {
    render(zh(<TaskPage taskId={view().task.task_id} client={client()} />));
    await screen.findByText(/標的 板橋-A × 三重-B/);
    expect(screen.getByText("技術紀錄")).toBeInTheDocument();
    // revision_id and the canonical subject name are preserved but not in the main view.
    expect(screen.getByText("r1")).toBeInTheDocument();
    expect(screen.getByText("r1")).not.toBeVisible();
    expect(screen.getByText(view().subject_id as string)).not.toBeVisible();
    // The finding list moved inside the fold.
    expect(screen.getByText("本次回覆對應的檢核紀錄")).not.toBeVisible();
  });
});

describe("Export and approval main copy", () => {
  it("keeps the idempotency mechanism out of the export panel's main flow", () => {
    render(zh(<ExportPanel api={{} as never} jobId="job-1" basis={basis()} />));
    const mechanism = screen.getByText(/此面板不重新計算任何數值/);
    expect(mechanism).toBeInTheDocument();
    expect(mechanism).not.toBeVisible();
    expect(screen.getByText(/切換格式會以新的識別碼建立新的匯出作業/)).not.toBeVisible();
    expect(screen.getByText("技術說明")).toBeVisible();
    // The real flow stays in front: format, mode and the generate action.
    expect(screen.getByText("檔案格式")).toBeVisible();
    expect(screen.getByText("申請版本")).toBeVisible();
    expect(screen.getByRole("button", { name: /產生並下載/ })).toBeVisible();
  });

  it("shows a named approval status and folds actors and policy versions into 版本證據", () => {
    const submitted = serverBasis({
      approval: {
        approval_id: "appr-1",
        job_id: "job-1",
        run: serverBasis().run,
        binding: {
          calculation_snapshot_digest: "b".repeat(64),
          template_bundle: basis().templateBundle,
          workbook_hashes: {
            table_3: "e".repeat(64),
            table_4: "f".repeat(64),
            table_5: "0".repeat(64),
          },
          readiness_policy_version: "readiness-v1",
        },
        status: "submitted",
        submitted_by: { actor_id: "11111111-1111-1111-1111-111111111111", kind: "human" },
        submitted_at: 1_757_600_000,
        payload_digest: "1".repeat(64),
        decision: null,
      },
    });
    render(
      zh(
        <ApprovalPanel
          api={{} as never}
          jobId="job-1"
          state={{ kind: "ready", basis: submitted }}
          onRefresh={vi.fn()}
        />,
      ),
    );
    expect(screen.getByText(/已送核（.*），等待具核准權限人員核准/)).toBeVisible();
    // The actor UUID stays available as version evidence, never as main copy.
    const actor = screen.getByText("11111111-1111-1111-1111-111111111111");
    expect(actor).toBeInTheDocument();
    expect(actor).not.toBeVisible();
    for (const policy of screen.getAllByText("readiness-v1", { exact: false }))
      expect(policy).not.toBeVisible();
    expect(screen.getByText("版本證據（識別碼與綁定雜湊）")).toBeInTheDocument();
  });
});
