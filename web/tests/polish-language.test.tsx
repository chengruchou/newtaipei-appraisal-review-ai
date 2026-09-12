/** Mocked component unit regression only; no real-case or backend acceptance. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { HumanResponse, ReviewClient, ReviewSessionView } from "@/api/client";
import { ServiceError, TransportError, type ServiceErrorCode } from "@/api/problems";
import { ResponseForm } from "@/features/ResponseForm";
import { TaskPage } from "@/features/TaskPage";
import { LanguageProvider } from "@/ui/Language";
import { DataModeNotice } from "@/ui/DataModeNotice";
import { ValueAuthority } from "@/ui/Authority";
import { CitationItem } from "@/ui/Evidence";
import { correctionView, subjectView, unlocatable, view } from "./fixtures";

it("confirms the exact Chinese correction preview without changing raw confidence or evidence", async () => {
  const submit = vi.fn().mockRejectedValue(new TransportError("Unit transport failure"));
  const user = userEvent.setup();
  render(
    <LanguageProvider language="zh">
      <ResponseForm
        view={correctionView()}
        subject={subjectView()}
        client={{ submitResponse: submit } as unknown as ReviewClient}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={() => "unit-key"}
      />
    </LanguageProvider>,
  );
  expect(screen.getByText(/所需類型：數值。單位：m/)).toBeVisible();
  await user.type(screen.getByLabelText("更正值"), "12");
  await user.click(screen.getByRole("button", { name: "檢視並準備提交" }));
  expect(submit).not.toHaveBeenCalled();
  expect(screen.getByLabelText("送出前確認")).toHaveTextContent("擬更正值（尚未採納）: 12 m");
  expect(screen.getByText(/更正不會自動核准/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "確認送出" }));
  expect(await screen.findByRole("button", { name: "查詢提交狀態" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "以同一內容重送" })).not.toBeInTheDocument();
  const command = submit.mock.calls[0]![1] as HumanResponse;
  expect(command.correction?.proposed).toMatchObject({
    confidence: 0,
    value: { value: 12, unit: "m" },
    evidence: subjectView().observation.evidence,
  });
  expect(command.idempotency_key).toBe("unit-key");
  expect(Object.isFrozen(command.correction?.proposed?.evidence)).toBe(true);
});

it.each<[ServiceErrorCode, number, string]>([
  ["unauthorized", 403, "目前沒有此操作的權限"],
  ["not_found", 404, "目前無法取得此任務"],
  ["version_conflict", 409, "任務或案件版本已更新"],
  ["invalid_request", 422, "服務未接受這份回覆"],
  ["capability_unavailable", 503, "目前服務尚未提供此功能"],
  ["execution_failed", 500, "服務無法完成這次操作"],
])(
  "explains canonical %s in Chinese while preserving refusal behavior",
  async (code, status, title) => {
    const submit = vi.fn().mockRejectedValue(new ServiceError(code, status));
    const user = userEvent.setup();
    render(
      <LanguageProvider language="zh">
        <ResponseForm
          view={view()}
          client={{ submitResponse: submit } as unknown as ReviewClient}
          onCommitted={vi.fn()}
          onReload={vi.fn()}
        />
      </LanguageProvider>,
    );
    await user.click(screen.getByRole("button", { name: "檢視並準備提交" }));
    await user.click(screen.getByRole("button", { name: "確認送出" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(title);
    expect(screen.queryByRole("button", { name: "以同一內容重送" })).not.toBeInTheDocument();
    expect(submit).toHaveBeenCalledTimes(1);
  },
);

it("uses Chinese task read errors and an explicit reload", async () => {
  const readTask = vi.fn().mockRejectedValue(new ServiceError("capability_unavailable", 503));
  const user = userEvent.setup();
  render(
    <LanguageProvider language="zh">
      <TaskPage taskId={view().task.task_id} client={{ readTask } as unknown as ReviewClient} />
    </LanguageProvider>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("目前服務尚未提供此功能");
  await user.click(screen.getByRole("button", { name: "重新讀取此任務" }));
  expect(readTask).toHaveBeenCalledTimes(2);
});

it.each<[ReviewSessionView["data_mode"], string]>([
  ["local_original", "真實本機原件"],
  ["synthetic", "隔離模擬資料"],
  ["unspecified", "資料來源模式尚未指定"],
])("labels %s without upgrading source mode to acceptance", (mode, label) => {
  render(
    <LanguageProvider language="zh">
      <DataModeNotice mode={mode} />
    </LanguageProvider>,
  );
  expect(screen.getByText(new RegExp(label))).toBeVisible();
  if (mode === "local_original")
    expect(screen.getByText(/不代表已執行模型、正式核准或真實個案驗收通過/)).toBeVisible();
});

it("distinguishes missing values from no field geometry in Chinese", () => {
  const subject = subjectView();
  render(
    <LanguageProvider language="zh">
      <ValueAuthority
        change={{
          schema_version: "service-v1",
          subject_id: subject.subject_id,
          original: { ...subject.observation, state: "missing", value: null },
          proposed: null,
          corrected: null,
          corrected_by: null,
        }}
      />
      <CitationItem citation={unlocatable} />
    </LanguageProvider>,
  );
  expect(screen.getByText("來源中缺少此值")).toBeVisible();
  expect(screen.getByText(/缺少可用的區域座標，因此不標示框線/)).toBeVisible();
});
