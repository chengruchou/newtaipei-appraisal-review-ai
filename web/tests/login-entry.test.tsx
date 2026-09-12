/** App + fake-fetch unit regression for the registration/login entrance. No real-service claim. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  vi.stubEnv("VITE_API_BASE_URL", "");
  vi.stubEnv("VITE_LOCAL_ORIGINAL_PREVIEW_URL", "");
});

import { App } from "@/App";

const NEUTRAL = "驗證信已寄出（若信箱可用），請於 10 分鐘內完成驗證。";
const GENERIC_LOGIN_REFUSAL = "帳號或密碼錯誤，或帳號尚未註冊。";
const GRANT_TOKEN = "unit-grant-token-1";
const ACTOR = "reviewer@example.com";
const PASSWORD = "unit-password-01";

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
    loginStatus: 200,
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
        json({
          token: GRANT_TOKEN,
          expires_at: "2026-09-13T03:00:00+08:00",
          actor_id: ACTOR,
          password_set: true,
        }),
      );
    }
    if (url === "/v1/auth/login" && method === "POST") {
      if (state.loginStatus !== 200) return Promise.resolve(json({}, state.loginStatus));
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

function callsTo(fetch: ReturnType<typeof wire>["fetch"], path: string) {
  return fetch.mock.calls.filter(([input]) => requestUrl(input) === path);
}

async function signInByPassword(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("電子郵件"), ACTOR);
  await user.type(screen.getByLabelText("密碼"), PASSWORD);
  await user.click(screen.getByRole("button", { name: "登入" }));
  await screen.findByRole("heading", { name: "案件清單", level: 1 });
}

/** Walks the staged flow up to the code + set-password step (register or reset). */
async function reachCodeStage(
  user: ReturnType<typeof userEvent.setup>,
  entry: "註冊新帳號" | "忘記密碼",
) {
  await user.click(screen.getByRole("button", { name: entry }));
  await user.type(screen.getByLabelText("電子郵件"), ACTOR);
  await user.click(screen.getByRole("button", { name: "寄送驗證信" }));
  await screen.findByText(NEUTRAL);
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

describe("registration/login entrance (unit regression)", () => {
  it("signs in with email and password, stores the bearer token, and never echoes the password", async () => {
    const { fetch } = wire();
    const user = userEvent.setup();
    render(<App />);
    await signInByPassword(user);
    expect(sessionStorage.getItem("workbench.session-token")).toBe(GRANT_TOKEN);
    expect((await screen.findAllByText(ACTOR)).length).toBeGreaterThan(0);
    const loginCalls = callsTo(fetch, "/v1/auth/login");
    expect(loginCalls).toHaveLength(1);
    expect(JSON.parse(loginCalls[0]?.[1]?.body as string)).toEqual({
      email: ACTOR,
      password: PASSWORD,
    });
    // The password is never echoed back into the page.
    expect(screen.queryByText(new RegExp(PASSWORD))).not.toBeInTheDocument();
  });

  it("refuses unknown email and wrong password with one identical generic sentence", async () => {
    const { state } = wire();
    state.loginStatus = 403;
    const user = userEvent.setup();
    render(<App />);
    // Attempt 1: an address that has never registered.
    await user.type(screen.getByLabelText("電子郵件"), "nobody-here@example.com");
    await user.type(screen.getByLabelText("密碼"), "whatever-password");
    await user.click(screen.getByRole("button", { name: "登入" }));
    const firstRefusal = (await screen.findByRole("alert")).textContent;
    // Attempt 2: a "known" address with a wrong password — the very same 403.
    await user.clear(screen.getByLabelText("電子郵件"));
    await user.type(screen.getByLabelText("電子郵件"), ACTOR);
    await user.type(screen.getByLabelText("密碼"), "wrong-password-99");
    await user.click(screen.getByRole("button", { name: "登入" }));
    const secondRefusal = (await screen.findByRole("alert")).textContent;
    expect(firstRefusal).toBe(GENERIC_LOGIN_REFUSAL);
    expect(secondRefusal).toBe(GENERIC_LOGIN_REFUSAL);
    // No second branch anywhere: nothing distinguishes the unknown address.
    expect(screen.queryByText(/帳號不存在|查無|已註冊的信箱|信箱未註冊/)).not.toBeInTheDocument();
    expect(sessionStorage.getItem("workbench.session-token")).toBeNull();
  });

  it("registers through request-code then verify with new_password, and signs in", async () => {
    const { fetch } = wire();
    const user = userEvent.setup();
    render(<App />);
    await reachCodeStage(user, "註冊新帳號");
    await screen.findByRole("heading", { name: "註冊新帳號" });
    await user.type(screen.getByLabelText("驗證碼"), "654321");
    await user.type(screen.getByLabelText("設定密碼"), PASSWORD);
    await user.type(screen.getByLabelText("確認密碼"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "完成註冊並登入" }));
    await screen.findByRole("heading", { name: "案件清單", level: 1 });
    expect(sessionStorage.getItem("workbench.session-token")).toBe(GRANT_TOKEN);
    const verifyCalls = callsTo(fetch, "/v1/auth/verify");
    expect(verifyCalls).toHaveLength(1);
    expect(JSON.parse(verifyCalls[0]?.[1]?.body as string)).toEqual({
      email: ACTOR,
      code: "654321",
      new_password: PASSWORD,
    });
    // Neither the code nor the password is echoed anywhere.
    expect(screen.queryByText(/654321/)).not.toBeInTheDocument();
  });

  it("blocks short or mismatched passwords locally and never calls the verify endpoint", async () => {
    const { state, fetch } = wire();
    const user = userEvent.setup();
    render(<App />);
    await reachCodeStage(user, "註冊新帳號");
    await user.type(screen.getByLabelText("驗證碼"), "654321");
    // Too short (7 characters): refused inline, no request.
    await user.type(screen.getByLabelText("設定密碼"), "short-7");
    await user.type(screen.getByLabelText("確認密碼"), "short-7");
    await user.click(screen.getByRole("button", { name: "完成註冊並登入" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("密碼長度至少 8 個字元。");
    // Long enough but the two entries disagree: refused inline, no request.
    await user.clear(screen.getByLabelText("設定密碼"));
    await user.clear(screen.getByLabelText("確認密碼"));
    await user.type(screen.getByLabelText("設定密碼"), PASSWORD);
    await user.type(screen.getByLabelText("確認密碼"), `${PASSWORD}x`);
    await user.click(screen.getByRole("button", { name: "完成註冊並登入" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("兩次輸入的密碼不一致。");
    expect(callsTo(fetch, "/v1/auth/verify")).toHaveLength(0);
    expect(sessionStorage.getItem("workbench.session-token")).toBeNull();
    // A rate-limited resend keeps its own sentence.
    state.requestCodeStatus = 429;
    await user.click(screen.getByRole("button", { name: "重新寄送驗證信" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("請稍後再試。");
  });

  it("clears the code and both password fields when the service refuses the verify step", async () => {
    const { state, fetch } = wire();
    state.verifyStatus = 403;
    const user = userEvent.setup();
    render(<App />);
    await reachCodeStage(user, "註冊新帳號");
    await user.type(screen.getByLabelText("驗證碼"), "000000");
    await user.type(screen.getByLabelText("設定密碼"), PASSWORD);
    await user.type(screen.getByLabelText("確認密碼"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "完成註冊並登入" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("驗證碼無效或已過期。");
    expect(callsTo(fetch, "/v1/auth/verify")).toHaveLength(1);
    expect(screen.getByLabelText("驗證碼")).toHaveValue("");
    expect(screen.getByLabelText("設定密碼")).toHaveValue("");
    expect(screen.getByLabelText("確認密碼")).toHaveValue("");
    expect(sessionStorage.getItem("workbench.session-token")).toBeNull();
  });

  it("reuses the staged flow for forgot-password with reset wording, neutrally for any address", async () => {
    const { fetch } = wire();
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "忘記密碼" }));
    await screen.findByRole("heading", { name: "重設密碼" });
    // The neutral sentence is the same for any address — never an existence oracle.
    await user.type(screen.getByLabelText("電子郵件"), "nobody-here@example.com");
    await user.click(screen.getByRole("button", { name: "寄送驗證信" }));
    await screen.findByText(NEUTRAL);
    await user.click(screen.getByRole("button", { name: "使用其他信箱" }));
    await user.clear(screen.getByLabelText("電子郵件"));
    await user.type(screen.getByLabelText("電子郵件"), ACTOR);
    await user.click(screen.getByRole("button", { name: "寄送驗證信" }));
    expect(await screen.findByText(NEUTRAL)).toBeVisible();
    expect(screen.queryByText(/已註冊|不存在|查無/)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("驗證碼"), "654321");
    await user.type(screen.getByLabelText("設定密碼"), PASSWORD);
    await user.type(screen.getByLabelText("確認密碼"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "重設密碼並登入" }));
    await screen.findByRole("heading", { name: "案件清單", level: 1 });
    expect(sessionStorage.getItem("workbench.session-token")).toBe(GRANT_TOKEN);
    const verifyCalls = callsTo(fetch, "/v1/auth/verify");
    expect(verifyCalls).toHaveLength(1);
    expect(JSON.parse(verifyCalls[0]?.[1]?.body as string)).toEqual({
      email: ACTOR,
      code: "654321",
      new_password: PASSWORD,
    });
  });

  it("logout revokes the session on the service and clears the local token", async () => {
    const { state } = wire();
    const user = userEvent.setup();
    render(<App />);
    await signInByPassword(user);
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
