/** App + fake-fetch unit regression for case intake and materials. No real-service claim. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  vi.stubEnv("VITE_API_BASE_URL", "");
  vi.stubEnv("VITE_LOCAL_ORIGINAL_PREVIEW_URL", "");
});

import { App } from "@/App";
import { sha256Hex } from "@/api/exports";

const TOKEN = "unit-intake-session";
const CASE_ID = "case-2026-000123";
const DEMO_CASE_ID = "case-demo-0001";
// JobReference.job_id is format "uuid" in the published schema.
const DEMO_JOB_ID = "0b6ee0f6-1111-4111-8111-000000000001";

function requestUrl(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function demoContext() {
  const job = { schema_version: "service-v1", case_id: DEMO_CASE_ID, job_id: DEMO_JOB_ID };
  return {
    schema_version: "service-v1",
    job,
    revision: {
      schema_version: "service-v1",
      case_id: DEMO_CASE_ID,
      material_digest: "d".repeat(64),
      revision_id: "rev-1",
    },
    identity: {
      case_id: DEMO_CASE_ID,
      version: "conditions-v1",
      district: "板橋區",
      zone: "單元區段",
      land_use_category: "住宅",
      effective_date: "2026-01-01",
    },
  };
}

function wire(options: { demoJobs?: boolean } = {}) {
  const state = {
    uploadStatus: 201,
    materials: [] as unknown[],
    uploads: 0,
  };
  const session = {
    schema_version: "service-v1",
    actor: { schema_version: "service-v1", actor_id: "intake-actor", kind: "human" },
    data_mode: "synthetic",
    configured_jobs: options.demoJobs
      ? [{ schema_version: "service-v1", case_id: DEMO_CASE_ID, job_id: DEMO_JOB_ID }]
      : [],
  };
  const fetch = vi.fn<typeof globalThis.fetch>(async (input, init) => {
    const url = requestUrl(input);
    const method = init?.method ?? "GET";
    if (url === "/v1/review-session") return json(session);
    if (url === "/v1/session" && method === "GET")
      return json({ actor_id: "intake-actor", kind: "fixture", expires_at: null });
    if (url === "/v1/session" && method === "DELETE") return new Response(null, { status: 204 });
    if (url === `/v1/review-jobs/${DEMO_JOB_ID}/context`) return json(demoContext());
    if (url === `/v1/review-jobs/${DEMO_JOB_ID}`)
      return json({
        schema_version: "service-v1",
        job: { schema_version: "service-v1", case_id: DEMO_CASE_ID, job_id: DEMO_JOB_ID },
        job_status: "waiting_for_human",
      });
    if (url === "/v1/cases" && method === "POST") {
      const command = JSON.parse(init?.body as string) as Record<string, unknown>;
      return json(
        {
          case_id: CASE_ID,
          title: command.title,
          district: command.district,
          created_at: "2026-09-12T23:00:00+08:00",
        },
        201,
      );
    }
    if (url === `/v1/cases/${CASE_ID}/materials` && method === "POST") {
      state.uploads += 1;
      if (state.uploadStatus !== 201) return json({}, state.uploadStatus);
      const bytes = init?.body as ArrayBuffer;
      const record = {
        material_id: `mat-${state.uploads}`,
        sha256: await sha256Hex(bytes),
        size: bytes.byteLength,
        filename: new Headers(init?.headers).get("X-Upload-Filename"),
        content_type: new Headers(init?.headers).get("Content-Type"),
      };
      state.materials.push(record);
      return json(record, 201);
    }
    if (url === `/v1/cases/${CASE_ID}/materials` && method === "GET")
      return json({ materials: state.materials });
    throw new Error(`Unexpected unit request: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  return { state, fetch };
}

async function createCase(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "案件清單", level: 1 });
  await user.type(screen.getByLabelText("案件名稱"), "測試案件");
  await user.type(screen.getByLabelText("行政區"), "板橋區");
  await user.click(screen.getByRole("button", { name: "建立案件" }));
  // The display id appears in both the created-case row and the materials panel.
  await screen.findAllByText(/顯示編號 26000123/);
}

beforeEach(() => {
  sessionStorage.clear();
  window.localStorage.clear();
  sessionStorage.setItem("workbench.session-token", TOKEN);
  window.history.replaceState({}, "", "/");
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});
afterAll(() => vi.unstubAllEnvs());

describe("case intake and materials (unit regression)", () => {
  it("labels configured demo jobs as synthetic with a shortened display id", async () => {
    wire({ demoJobs: true });
    render(<App />);
    expect(await screen.findByText("示範案件 DEMO0001")).toBeVisible();
    expect(screen.getByText("合成示範")).toBeVisible();
    expect(screen.getByRole("link", { name: /開啟案件/ })).toHaveAttribute(
      "href",
      `/jobs/${DEMO_JOB_ID}/progress`,
    );
  });

  it("creates a case and uploads a material whose SHA-256 is verified locally", async () => {
    const { fetch } = wire();
    const user = userEvent.setup();
    render(<App />);
    await createCase(user);
    const file = new File(["hello"], "report.pdf", { type: "application/pdf" });
    const expectedSha = await sha256Hex(new TextEncoder().encode("hello").buffer);
    await user.upload(screen.getByLabelText("上傳材料檔案（上限 64MB）"), file);
    expect(await screen.findByText(/已保存，SHA-256 已核對。/)).toBeVisible();
    // The digest shown is the service's stored digest, verified against local bytes.
    expect((await screen.findAllByTitle(expectedSha)).length).toBeGreaterThan(0);
    const uploadCall = fetch.mock.calls.find(
      ([input, init]) =>
        requestUrl(input) === `/v1/cases/${CASE_ID}/materials` && init?.method === "POST",
    )!;
    const headers = new Headers(uploadCall[1]?.headers);
    expect(headers.get("X-Upload-Filename")).toBe("report.pdf");
    expect(headers.get("Content-Type")).toBe("application/pdf");
    expect(headers.get("X-Idempotency-Key")).toMatch(/^wb-/);
    expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
    expect((uploadCall[1]?.body as ArrayBuffer).byteLength).toBe(5);
    // The list afterwards is the service's own answer, not a local assumption.
    expect(await screen.findByText("report.pdf")).toBeVisible();
  });

  it("shows a sign-in prompt when the service refuses an upload with 403", async () => {
    const { state } = wire();
    const user = userEvent.setup();
    render(<App />);
    await createCase(user);
    state.uploadStatus = 403;
    await user.upload(
      screen.getByLabelText("上傳材料檔案（上限 64MB）"),
      new File(["x"], "again.pdf", { type: "application/pdf" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "未登入或無存取權限，請重新登入後再試。",
    );
  });

  it("reports a 413 refusal as too large without pretending it was saved", async () => {
    const { state } = wire();
    const user = userEvent.setup();
    render(<App />);
    await createCase(user);
    state.uploadStatus = 413;
    await user.upload(
      screen.getByLabelText("上傳材料檔案（上限 64MB）"),
      new File(["y"], "big.pdf", { type: "application/pdf" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "服務拒絕此檔案：太大（上限 64MB），未保存。",
    );
    expect(screen.queryByText(/已保存，SHA-256 已核對/)).not.toBeInTheDocument();
  });
});
