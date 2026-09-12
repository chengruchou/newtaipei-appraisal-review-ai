import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  ExportServiceError,
  type CreateExportCommand,
  type ExportOperation,
  type ExportsApi,
} from "@/api/exports";
import { TransportError } from "@/api/problems";
import { ExportPanel, type ExportBasis } from "@/features/ExportPanel";
import { LanguageProvider } from "@/ui/Language";
import { artifact, basis, operation } from "./export-fixtures";

function api(overrides: Partial<ExportsApi> = {}): ExportsApi {
  return {
    readBasis: vi.fn(() => Promise.reject(new Error("readBasis not scripted"))),
    createExport: vi.fn(() => Promise.reject(new Error("createExport not scripted"))),
    readExport: vi.fn(() => Promise.reject(new Error("readExport not scripted"))),
    downloadArtifact: vi.fn(() => Promise.reject(new Error("downloadArtifact not scripted"))),
    ...overrides,
  };
}

function show(service: ExportsApi, value: ExportBasis | null = basis()) {
  return render(
    <LanguageProvider language="zh">
      <ExportPanel api={service} jobId="job-1" basis={value} pollIntervalMs={10} />
    </LanguageProvider>,
  );
}

const generateButton = () => screen.getByRole("button", { name: /產生並下載/ });

describe("export panel request identity", () => {
  it("offers exactly one format and mints a new key when the format changes", async () => {
    const created: CreateExportCommand[] = [];
    const service = api({
      createExport: vi.fn((_job: string, sent: CreateExportCommand) => {
        created.push(sent);
        return Promise.resolve(
          operation({
            export_id: `exp-${created.length}`,
            export_format: sent.export_format,
            status: "succeeded",
          }),
        );
      }),
    });
    const user = userEvent.setup();
    show(service);
    const xlsx = screen.getByRole("radio", { name: /Excel \(\.xlsx\)/ });
    const pdf = screen.getByRole("radio", { name: /PDF \(\.pdf\)/ });
    expect(xlsx).toBeChecked();
    expect(pdf).not.toBeChecked();
    await user.click(generateButton());
    await screen.findByText("已完成");
    await user.click(pdf);
    expect(pdf).toBeChecked();
    expect(xlsx).not.toBeChecked();
    await user.click(generateButton());
    await waitFor(() => expect(created).toHaveLength(2));
    expect(created[0]?.export_format).toBe("xlsx");
    expect(created[1]?.export_format).toBe("pdf");
    expect(created[1]?.idempotency_key).not.toBe(created[0]?.idempotency_key);
  });

  it("retries an unknown POST outcome with the same key and payload, never a silent new key", async () => {
    const calls: CreateExportCommand[] = [];
    const service = api({
      createExport: vi.fn((_job: string, sent: CreateExportCommand) => {
        calls.push(sent);
        return calls.length === 1
          ? Promise.reject(new TransportError("The service did not answer in time."))
          : Promise.resolve(operation({ status: "succeeded" }));
      }),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(generateButton());
    await screen.findByText(/無法確認匯出請求是否已被受理/);
    await user.click(screen.getByRole("button", { name: "以同一請求重試" }));
    await screen.findByText("已完成");
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual(calls[0]);
  });

  it("explains a replay conflict as changed content and asks for a fresh generation", async () => {
    const service = api({
      createExport: vi.fn(() =>
        Promise.reject(new ExportServiceError("version_conflict", 409, null)),
      ),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(generateButton());
    await screen.findByText(/格式或內容已變更，請重新產生/);
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
  });
});

describe("export panel operation tracking", () => {
  it("polls the real state to succeeded and renders a download per delivered table", async () => {
    const reads: ExportOperation[] = [
      operation({ status: "running" }),
      operation({
        status: "succeeded",
        effective_mode: "draft",
        tables: [
          { table: "table_3", delivered: true, artifact_id: "a3", problem: null },
          { table: "table_4", delivered: true, artifact_id: "a4", problem: null },
          { table: "table_5", delivered: true, artifact_id: "a5", problem: null },
        ],
        artifacts: [
          artifact({ artifact_id: "a3", filename: "表3.xlsx" }),
          artifact({ artifact_id: "a4", filename: "表4.xlsx" }),
          artifact({ artifact_id: "a5", filename: "表5.xlsx" }),
        ],
      }),
    ];
    let readCount = 0;
    const service = api({
      createExport: vi.fn(() => Promise.resolve(operation({ status: "queued" }))),
      readExport: vi.fn(() => {
        const next = reads[Math.min(readCount, reads.length - 1)];
        readCount += 1;
        return Promise.resolve(next as ExportOperation);
      }),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(generateButton());
    await screen.findByText("已排入佇列");
    await screen.findByText("已完成");
    const downloads = screen.getAllByRole("button", { name: /^下載 表/ });
    expect(downloads).toHaveLength(3);
    // Terminal state ends polling; no further reads and no invented percentages.
    const settled = readCount;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(readCount).toBe(settled);
    expect(document.body.textContent).not.toMatch(/\d+\s*%/);
  });

  it("labels a draft on the panel and beside every download", async () => {
    const service = api({
      createExport: vi.fn(() =>
        Promise.resolve(
          operation({
            status: "succeeded",
            effective_mode: "draft",
            tables: [{ table: "table_4", delivered: true, artifact_id: "a4", problem: null }],
            artifacts: [artifact({ artifact_id: "a4", filename: "表4.xlsx" })],
          }),
        ),
      ),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(generateButton());
    await screen.findByText("已完成");
    expect(screen.getAllByText("草稿（未正式核定）")).toHaveLength(2);
  });

  it("shows a partial delivery as delivered and missing tables with reasons", async () => {
    const service = api({
      createExport: vi.fn(() =>
        Promise.resolve(
          operation({
            status: "partial",
            tables: [
              { table: "table_3", delivered: true, artifact_id: "a3", problem: null },
              {
                table: "table_4",
                delivered: false,
                artifact_id: null,
                problem: { code: "missing_factor_values", message: "缺少必要因素值" },
              },
              { table: "table_5", delivered: false, artifact_id: null, problem: null },
            ],
            artifacts: [artifact({ artifact_id: "a3", filename: "表3.xlsx" })],
            blockers: ["missing_factor: road_width", "missing_factor: road_width"],
          }),
        ),
      ),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(generateButton());
    expect(await screen.findAllByText("部分交付")).not.toHaveLength(0);
    expect(screen.getByRole("button", { name: "下載 表3.xlsx" })).toBeInTheDocument();
    expect(screen.getAllByText(/缺少必要因素值/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/服務未提供原因/).length).toBeGreaterThan(0);
    const blockerBox = screen.getByText("尚未解除的限制").closest("div");
    expect(blockerBox).not.toBeNull();
    expect(within(blockerBox as HTMLElement).getAllByRole("listitem")).toHaveLength(1);
  });

  it("renders an unavailable export capability honestly, with the service's own message", async () => {
    const service = api({
      createExport: vi.fn(() =>
        Promise.reject(
          new ExportServiceError("capability_unavailable", 503, "Export routes are not deployed."),
        ),
      ),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(generateButton());
    await screen.findByText(/匯出功能尚未配置/);
    expect(screen.getByText(/Export routes are not deployed\./)).toBeInTheDocument();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^下載/ })).not.toBeInTheDocument();
  });

  it("treats a lapsed download grant as re-authorization, with a state re-read action", async () => {
    const succeeded = operation({
      status: "succeeded",
      tables: [{ table: "table_3", delivered: true, artifact_id: "a3", problem: null }],
      artifacts: [artifact({ artifact_id: "a3", filename: "表3.xlsx" })],
    });
    const readExport = vi.fn(() => Promise.resolve(succeeded));
    const service = api({
      createExport: vi.fn(() => Promise.resolve(succeeded)),
      readExport,
      downloadArtifact: vi.fn(() => Promise.reject(new ExportServiceError("unauthorized", 403))),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(generateButton());
    await user.click(await screen.findByRole("button", { name: "下載 表3.xlsx" }));
    await screen.findByText(/下載授權已過期，請重新授權後再試/);
    await user.click(screen.getByRole("button", { name: "重新讀取匯出狀態" }));
    await waitFor(() => expect(readExport).toHaveBeenCalledTimes(1));
  });

  it("says when the job state cannot support an export request yet", () => {
    show(api(), null);
    expect(screen.getByText(/暫時無法建立匯出/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /產生並下載/ })).not.toBeInTheDocument();
  });
});
