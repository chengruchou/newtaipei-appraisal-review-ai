/** App + fake-fetch unit regression for the email-login entrance. No real-service claim. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  vi.stubEnv("VITE_API_BASE_URL", "");
  vi.stubEnv("VITE_LOCAL_ORIGINAL_PREVIEW_URL", "");
});

import { App } from "@/App";

const NEUTRAL = "若信箱可用，驗證碼已寄出（10 分鐘內有效）。";
const GRANT_TOKEN = "unit-grant-token-1";
const ACTOR = "reviewer@example.com";

function requestUrl(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function reviewSession() {
  return {
    schema_version: "service-v1",
    actor: { schema_version: "service-v1", actor_id: ACTOR, kind: "human" },
    data_mode: "unspecified",
    configured_jobs: [],
  };
}

function wire() {
  const state = {
    requestCodeStatus: 202,
    verifyStatus: 200,
    revokedWith: [] as Array<string | null>,
  };
  const fetch = vi.fn<typeof globalThis.fetch>((input, init) => {
    const url = requestUrl(input);
    const method = init?.method ?? "GET";
    if (url === "/v1/auth/request-code" && method === "POST")
      return Promise.resolve(json({ accepted: true }, state.requestCodeStatus));
    if (url === "/v1/auth/verify" && method === "POST") {
      if (state.verifyStatus !== 200) return Promise.resolve(json({}, state.verifyStatus));
      return Promise.resolve(
        json({ token: GRANT_TOKEN, expires_at: "2026-09-13T03:00:00+08:00", actor_id: ACTOR }),
      );
    }
    if (url === "/v1/review-session" && method === "GET")
      return Promise.resolve(json(reviewSession()));
    if (url === "/v1/session" && method === "GET")
      return Promise.resolve(
        json({
          actor_id: ACTOR,
          kind: "email",
          expires_at: "2026-09-13T03:00:00+08:00",
          permissions_summary: "review",
        }),
      );
    if (url === "/v1/session" && method === "DELETE") {
      state.revokedWith.push(new Headers(init?.headers).get("Authorization"));
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    return Promise.reject(new Error(`Unexpected unit request: ${method} ${url}`));
  });
  vi.stubGlobal("fetch", fetch);
  return { state, fetch };
}

async function signInByEmail(user: ReturnType<typeof userEvent.setup>, code = "654321") {
  await user.type(screen.getByLabelText("電子郵件"), ACTOR);
  await user.click(screen.getByRole("button", { name: "送出驗證碼" }));
  await screen.findByText(NEUTRAL);
  await user.type(screen.getByLabelText("驗證碼"), code);
  await user.click(screen.getByRole("button", { name: "驗證並登入" }));
  await screen.findByRole("heading", { name: "案件清單", level: 1 });
}

beforeEach(() => {
  sessionStorage.clear();
  window.localStorage.clear();
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

describe("email login entrance (unit regression)", () => {
  it("shows one neutral sentence for any address, never an account-existence answer", async () => {
    const { fetch } = wire();
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("電子郵件"), "known.person@example.com");
    await user.click(screen.getByRole("button", { name: "送出驗證碼" }));
    await screen.findByText(NEUTRAL);
    await user.click(screen.getByRole("button", { name: "使用其他信箱" }));
    await user.clear(screen.getByLabelText("電子郵件"));
    await user.type(screen.getByLabelText("電子郵件"), "nobody-here@example.com");
    await user.click(screen.getByRole("button", { name: "送出驗證碼" }));
    expect(await screen.findByText(NEUTRAL)).toBeVisible();
    const bodies = fetch.mock.calls
      .filter(([input]) => requestUrl(input) === "/v1/auth/request-code")
      .map(([, init]) => JSON.parse(init?.body as string) as { email: string });
    expect(bodies.map((body) => body.email)).toEqual([
      "known.person@example.com",
      "nobody-here@example.com",
    ]);
    // The UI has no second branch: both addresses saw exactly the same sentence.
    expect(screen.queryByText(/已註冊|不存在|查無/)).not.toBeInTheDocument();
  });

  it("verifies the code, stores the bearer token, and shows the session identity", async () => {
    wire();
    const user = userEvent.setup();
    render(<App />);
    await signInByEmail(user);
    expect(sessionStorage.getItem("workbench.session-token")).toBe(GRANT_TOKEN);
    expect((await screen.findAllByText(ACTOR)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/工作階段到期時間/)).toBeVisible();
    // The one-time code is never echoed back into the page.
    expect(screen.queryByText(/654321/)).not.toBeInTheDocument();
  });

  it("keeps the refusal generic on 403 and offers resend; a rate limit says try later", async () => {
    const { state } = wire();
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("電子郵件"), ACTOR);
    await user.click(screen.getByRole("button", { name: "送出驗證碼" }));
    await screen.findByText(NEUTRAL);
    state.verifyStatus = 403;
    await user.type(screen.getByLabelText("驗證碼"), "000000");
    await user.click(screen.getByRole("button", { name: "驗證並登入" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("驗證碼無效或已過期。");
    expect(sessionStorage.getItem("workbench.session-token")).toBeNull();
    state.requestCodeStatus = 429;
    await user.click(screen.getByRole("button", { name: "重新寄送驗證碼" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("請稍後再試。");
  });

  it("logout revokes the session on the service and clears the local token", async () => {
    const { state } = wire();
    const user = userEvent.setup();
    render(<App />);
    await signInByEmail(user);
    await user.click(screen.getByRole("button", { name: "登出並清除本機工作階段" }));
    await screen.findByRole("heading", { name: "登入審查工作台" });
    expect(sessionStorage.getItem("workbench.session-token")).toBeNull();
    expect(state.revokedWith).toEqual([`Bearer ${GRANT_TOKEN}`]);
  });

  it("keeps the advanced token-paste fallback working behind the collapsed entry", async () => {
    wire();
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByText("使用工作階段憑證（進階）"));
    await user.type(screen.getByLabelText("本機工作階段憑證"), "unit-fixture-token");
    await user.click(screen.getByRole("button", { name: "驗證並繼續" }));
    await screen.findByRole("heading", { name: "案件清單", level: 1 });
    expect(sessionStorage.getItem("workbench.session-token")).toBe("unit-fixture-token");
  });
});
