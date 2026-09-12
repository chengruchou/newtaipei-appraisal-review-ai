/** App + fake-fetch unit regression only. No browser or real-case acceptance claim. */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewClient, type ReviewSessionView, type TaskView } from "@/api/client";
import { App } from "@/App";
import { subjectView, view } from "./fixtures";

vi.hoisted(() => {
  vi.stubEnv("VITE_API_BASE_URL", "");
  vi.stubEnv("VITE_LOCAL_ORIGINAL_PREVIEW_URL", "http://127.0.0.1:18766");
});

const TOKEN_A = "unit-session-A";
const TOKEN_B = "unit-session-B";
const PAIR_A = "unit-pair-A";
const PAIR_B = "unit-pair-B";
const question = (actor: string) => `Unit task visible only to actor ${actor}`;
const excerpt = (actor: string) => `Unit source excerpt for actor ${actor}`;

function taskFor(actor: string): TaskView {
  const task = view();
  task.task.question = question(actor);
  task.task.run.revision.case_id = `unit-case-${actor}`;
  task.task.evidence = task.task.evidence.map((citation) => ({
    ...citation,
    excerpt: excerpt(actor),
  }));
  return task;
}
function subjectFor(actor: string) {
  const task = taskFor(actor);
  const subject = subjectView(task);
  const value = actor === "A" ? 101 : 202;
  subject.observation.value = { type: "number", value, unit: "m" };
  subject.observation.raw_text = `${value} m`;
  subject.observation.evidence = task.task.evidence;
  return subject;
}

function requestUrl(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

function session(actor: string): ReviewSessionView {
  return {
    schema_version: "service-v1",
    actor: { schema_version: "service-v1", actor_id: actor, kind: "human" },
    data_mode: "local_original",
    configured_jobs: [],
  };
}
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const unauthorized = () =>
  json(
    {
      schema_version: "service-v1",
      code: "unauthorized",
      message: "Service operation could not be completed.",
    },
    403,
  );

function deferred<T>() {
  let resolve: (value: T) => void = () => {
    throw new Error("Deferred unit response is not ready");
  };
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function wire() {
  const state: {
    actor: string;
    rejectSession: boolean;
    subjectResponse: Promise<Response> | null;
  } = { actor: "A", rejectSession: false, subjectResponse: null };
  const fetch = vi.fn<typeof globalThis.fetch>((input, init) => {
    const url = requestUrl(input);
    const bearer = new Headers(init?.headers).get("Authorization");
    const actor = bearer === `Bearer ${TOKEN_B}` ? "B" : state.actor;
    if (url === "/v1/review-session")
      return Promise.resolve(state.rejectSession ? unauthorized() : json(session(actor)));
    if (url.endsWith("/subject"))
      return state.subjectResponse ?? Promise.resolve(json(subjectFor(actor)));
    if (url === `/v1/review-tasks/${view().task.task_id}`)
      return Promise.resolve(json(taskFor(actor)));
    return Promise.reject(new Error(`Unexpected unit request: ${url}`));
  });
  vi.stubGlobal("fetch", fetch);
  return { state, fetch };
}

beforeEach(() => {
  sessionStorage.clear();
  window.localStorage.clear();
  sessionStorage.setItem("workbench.session-token", TOKEN_A);
  sessionStorage.setItem("workbench.original-pairing", PAIR_A);
  window.localStorage.setItem("workbench.language", "en");
  window.history.replaceState({}, "", `/tasks/${view().task.task_id}`);
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

describe("session and rendered-data isolation (unit regression)", () => {
  it("restores separate pairing through build-time configuration without putting credentials in the URL", async () => {
    const { fetch } = wire();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(question("A"));
    await user.click(screen.getByRole("button", { name: "Open source page 3" }));
    await screen.findByText(/The exact source page could not be loaded or verified/);
    const [input, init] = fetch.mock.calls.find(([input]) =>
      requestUrl(input).startsWith("http://127.0.0.1:18766/local-original/"),
    )!;
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${PAIR_A}`);
    expect(headers.get("X-Review-Session")).toBe(`Bearer ${TOKEN_A}`);
    expect(requestUrl(input)).not.toContain(PAIR_A);
    expect(requestUrl(input)).not.toContain(TOKEN_A);
    expect(fetch.mock.calls.some(([input]) => requestUrl(input).startsWith("/v1/documents/"))).toBe(
      false,
    );
  });

  it("disposes the old client and clears rendered task/evidence when session actor changes", async () => {
    const { state } = wire();
    const dispose = vi.spyOn(ReviewClient.prototype, "dispose");
    render(<App />);
    expect(await screen.findByText(question("A"))).toBeVisible();
    expect(screen.getByText(excerpt("A"))).toBeVisible();
    state.actor = "changed-actor";
    fireEvent.focus(window);
    expect(await screen.findByRole("alert")).toHaveTextContent("The session identity changed");
    expect(screen.queryByText(question("A"))).not.toBeInTheDocument();
    expect(screen.queryByText(excerpt("A"))).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review and submit" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Session token", { exact: true })).toHaveValue("");
    expect(sessionStorage.getItem("workbench.session-token")).toBeNull();
    expect(sessionStorage.getItem("workbench.original-pairing")).toBeNull();
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it("removes rendered content when a trusted session recheck is unauthorized", async () => {
    const { state } = wire();
    render(<App />);
    await screen.findByText(question("A"));
    state.rejectSession = true;
    fireEvent.focus(window);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This session is no longer authorized",
    );
    expect(screen.queryByText(question("A"))).not.toBeInTheDocument();
    expect(screen.queryByText(excerpt("A"))).not.toBeInTheDocument();
    expect(sessionStorage.getItem("workbench.original-pairing")).toBeNull();
  });

  it("keeps the current actor's data after an unchanged session recheck", async () => {
    const { fetch } = wire();
    const dispose = vi.spyOn(ReviewClient.prototype, "dispose");
    render(<App />);
    await screen.findByText(question("A"));
    fireEvent.focus(window);
    await waitFor(() =>
      expect(fetch.mock.calls.filter(([input]) => input === "/v1/review-session")).toHaveLength(2),
    );
    expect(screen.getByText(question("A"))).toBeVisible();
    expect(screen.getByText(excerpt("A"))).toBeVisible();
    expect(dispose).not.toHaveBeenCalled();
  });

  it("drops old rendered data and unsubmitted response when switching accounts", async () => {
    const { fetch } = wire();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(question("A"));
    await user.click(screen.getByRole("radio", { name: "Refuse to confirm" }));
    await user.click(screen.getByRole("button", { name: "Review and submit" }));
    expect(screen.getByRole("button", { name: "Yes, submit" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(screen.queryByText(question("A"))).not.toBeInTheDocument();
    expect(screen.queryByText(excerpt("A"))).not.toBeInTheDocument();
    expect(sessionStorage.getItem("workbench.original-pairing")).toBeNull();
    await user.type(screen.getByLabelText("Session token", { exact: true }), TOKEN_B);
    await user.type(screen.getByLabelText("Local original pairing code (optional)"), PAIR_B);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(question("B"))).toBeVisible();
    expect(screen.getByText(excerpt("B"))).toBeVisible();
    expect(screen.queryByText(question("A"))).not.toBeInTheDocument();
    expect(screen.queryByText(excerpt("A"))).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yes, submit" })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Confirm this observation" })).toBeChecked();
    expect(sessionStorage.getItem("workbench.session-token")).toBe(TOKEN_B);
    expect(sessionStorage.getItem("workbench.original-pairing")).toBe(PAIR_B);
    expect(fetch.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Open source page 3" }));
    await screen.findByText(/The exact source page could not be loaded or verified/);
    const [, init] = fetch.mock.calls.find(([input]) =>
      requestUrl(input).startsWith("http://127.0.0.1:18766/local-original/"),
    )!;
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${PAIR_B}`);
    expect(headers.get("X-Review-Session")).toBe(`Bearer ${TOKEN_B}`);
  });

  it("cannot restore account A's delayed subject after account B has connected", async () => {
    const { state, fetch } = wire();
    const pending = deferred<Response>();
    state.subjectResponse = pending.promise;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(question("A"));
    await waitFor(() =>
      expect(fetch.mock.calls.some(([input]) => requestUrl(input).endsWith("/subject"))).toBe(true),
    );
    const oldSignal = fetch.mock.calls.find(([input]) => requestUrl(input).endsWith("/subject"))![1]
      ?.signal;
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(oldSignal?.aborted).toBe(true);
    state.subjectResponse = null;
    await user.type(screen.getByLabelText("Session token", { exact: true }), TOKEN_B);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText(question("B"));
    await screen.findByText("202 m");
    await act(() => {
      pending.resolve(json(subjectFor("A")));
      return Promise.resolve();
    });
    expect(screen.queryByText(question("A"))).not.toBeInTheDocument();
    expect(screen.queryByText(excerpt("A"))).not.toBeInTheDocument();
    expect(screen.getByText(question("B"))).toBeVisible();
    expect(screen.queryByText("101 m")).not.toBeInTheDocument();
    expect(screen.getByText("202 m")).toBeVisible();
    expect(sessionStorage.getItem("workbench.original-pairing")).toBeNull();
  });

  it("disposes requests on unmount, so an old App cannot render into a new account", async () => {
    const { state, fetch } = wire();
    const pending = deferred<Response>();
    state.subjectResponse = pending.promise;
    const mounted = render(<App />);
    await screen.findByText(question("A"));
    await waitFor(() =>
      expect(fetch.mock.calls.some(([input]) => requestUrl(input).endsWith("/subject"))).toBe(true),
    );
    const signal = fetch.mock.calls.find(([input]) => requestUrl(input).endsWith("/subject"))![1]
      ?.signal;
    mounted.unmount();
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText(question("A"))).not.toBeInTheDocument();
    sessionStorage.setItem("workbench.session-token", TOKEN_B);
    sessionStorage.removeItem("workbench.original-pairing");
    state.subjectResponse = null;
    render(<App />);
    await screen.findByText(question("B"));
    await screen.findByText("202 m");
    await act(() => {
      pending.resolve(json(subjectFor("A")));
      return Promise.resolve();
    });
    expect(screen.queryByText(question("A"))).not.toBeInTheDocument();
    expect(screen.getByText(question("B"))).toBeVisible();
    expect(screen.queryByText("101 m")).not.toBeInTheDocument();
    expect(screen.getByText("202 m")).toBeVisible();
  });
});
