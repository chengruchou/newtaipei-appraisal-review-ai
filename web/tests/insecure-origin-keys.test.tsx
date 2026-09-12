/**
 * Production bug: on the plain-HTTP demo origin `crypto.randomUUID` is undefined, so
 * minting an idempotency key inside a click handler threw and the buttons did nothing.
 * These tests pin the two behaviours that fix it: minting falls back to
 * `getRandomValues`, and when even that is missing the reviewer sees an explicit
 * message instead of a dead button - with retry-same-key semantics untouched.
 */
import { webcrypto } from "node:crypto";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewClient } from "@/api/client";
import type { ResponseReceipt } from "@/api/client";
import type { CreateExportCommand, ExportsApi } from "@/api/exports";
import { ServiceError, TransportError } from "@/api/problems";
import { ExportPanel } from "@/features/ExportPanel";
import { ResponseForm } from "@/features/ResponseForm";
import { LanguageProvider } from "@/ui/Language";
import { basis, operation } from "./export-fixtures";
import { view } from "./fixtures";

const KEY = /^wb-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** What an insecure (plain-HTTP) origin actually provides: no randomUUID. */
function stubInsecureOriginCrypto() {
  vi.stubGlobal("crypto", {
    getRandomValues: (array: Uint8Array) => webcrypto.getRandomValues(array),
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function exportsApi(overrides: Partial<ExportsApi> = {}): ExportsApi {
  return {
    readBasis: vi.fn(() => Promise.reject(new Error("readBasis not scripted"))),
    createExport: vi.fn(() => Promise.reject(new Error("createExport not scripted"))),
    readExport: vi.fn(() => Promise.reject(new Error("readExport not scripted"))),
    downloadArtifact: vi.fn(() => Promise.reject(new Error("downloadArtifact not scripted"))),
    ...overrides,
  };
}

function showPanel(service: ExportsApi) {
  return render(
    <LanguageProvider language="zh">
      <ExportPanel api={service} jobId="job-1" basis={basis()} pollIntervalMs={10} />
    </LanguageProvider>,
  );
}

describe("ExportPanel on an origin without crypto.randomUUID", () => {
  it("still creates the export with a well-formed wb- v4 key", async () => {
    stubInsecureOriginCrypto();
    const created: CreateExportCommand[] = [];
    const service = exportsApi({
      createExport: vi.fn((_job: string, sent: CreateExportCommand) => {
        created.push(sent);
        return Promise.resolve(operation({ status: "succeeded" }));
      }),
    });
    const user = userEvent.setup();
    showPanel(service);
    await user.click(screen.getByRole("button", { name: /產生並下載/ }));
    await screen.findByText("已完成");
    expect(created).toHaveLength(1);
    expect(created[0]?.idempotency_key).toMatch(KEY);
  });

  it("retries an unknown outcome with the same fallback-minted key and payload", async () => {
    stubInsecureOriginCrypto();
    const calls: CreateExportCommand[] = [];
    const service = exportsApi({
      createExport: vi.fn((_job: string, sent: CreateExportCommand) => {
        calls.push(sent);
        return calls.length === 1
          ? Promise.reject(new TransportError("The service did not answer in time."))
          : Promise.resolve(operation({ status: "succeeded" }));
      }),
    });
    const user = userEvent.setup();
    showPanel(service);
    await user.click(screen.getByRole("button", { name: /產生並下載/ }));
    await screen.findByText(/無法確認匯出請求是否已被受理/);
    await user.click(screen.getByRole("button", { name: "以同一請求重試" }));
    await screen.findByText("已完成");
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual(calls[0]);
    expect(calls[0]?.idempotency_key).toMatch(KEY);
  });

  it("explains a failed key mint instead of a dead button, and sends nothing", async () => {
    vi.stubGlobal("crypto", {});
    const createExport = vi.fn(() => Promise.reject(new Error("createExport not scripted")));
    const service = exportsApi({ createExport });
    const user = userEvent.setup();
    showPanel(service);
    const button = screen.getByRole("button", { name: /產生並下載/ });
    await user.click(button);
    await screen.findByText(/無法產生操作識別碼，請更新瀏覽器或改用安全連線/);
    expect(createExport).not.toHaveBeenCalled();
    // The panel is not stuck: the button stays usable for a later attempt.
    expect(button).toBeEnabled();
  });
});

const receipt = {
  schema_version: "service-v1",
  task_id: "11111111-1111-1111-1111-111111111111",
  consumed_version: 1,
  task_state: "answered",
  action: "confirm",
  job: { schema_version: "service-v1", case_id: "case-1", job_id: "job-1" },
  job_status: "queued",
  revision: {
    schema_version: "service-v1",
    case_id: "case-1",
    revision_id: "r2",
    material_digest: "b".repeat(64),
  },
  resumed_run: null,
  superseded_task_ids: [],
} as unknown as ResponseReceipt;

function clientWith(submit: ReturnType<typeof vi.fn>): ReviewClient {
  return {
    submitResponse: submit,
    readResponse: vi.fn().mockRejectedValue(new ServiceError("not_found", 404)),
    readTask: vi.fn().mockResolvedValue(view()),
  } as unknown as ReviewClient;
}

function showForm(submit: ReturnType<typeof vi.fn>) {
  return render(
    <ResponseForm
      view={view()}
      client={clientWith(submit)}
      onCommitted={vi.fn()}
      onReload={vi.fn()}
    />,
  );
}

describe("ResponseForm on an origin without crypto.randomUUID", () => {
  it("submits with a well-formed fallback-minted key via the default mintKey", async () => {
    stubInsecureOriginCrypto();
    const submit = vi.fn().mockResolvedValue(receipt);
    const user = userEvent.setup();
    showForm(submit);
    await user.click(screen.getByRole("button", { name: /review and submit/i }));
    await user.click(screen.getByRole("button", { name: /yes, submit/i }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    const command = submit.mock.calls[0]?.[1] as { idempotency_key: string };
    expect(command.idempotency_key).toMatch(KEY);
  });

  it("explains a failed key mint instead of a dead submit button, and recovers", async () => {
    vi.stubGlobal("crypto", {});
    const submit = vi.fn().mockResolvedValue(receipt);
    const user = userEvent.setup();
    showForm(submit);
    const review = screen.getByRole("button", { name: /review and submit/i });
    await user.click(review);
    await screen.findByText(/An operation identifier could not be generated/);
    expect(submit).not.toHaveBeenCalled();
    expect(review).toBeEnabled();
    // The same button works once randomness exists again (e.g. after a browser update).
    stubInsecureOriginCrypto();
    await user.click(review);
    expect(
      screen.queryByText(/An operation identifier could not be generated/),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /yes, submit/i })).toBeInTheDocument();
  });
});
