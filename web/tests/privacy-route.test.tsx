import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "@/App";

const session = {
  schema_version: "service-v1",
  actor: { schema_version: "service-v1", actor_id: "local-reviewer", kind: "human" },
  data_mode: "unspecified",
  configured_jobs: [],
};
beforeEach(() => {
  window.localStorage.setItem("workbench.language", "en");
  sessionStorage.setItem("workbench.session-token", "issued-session");
});
const json = (body: unknown) =>
  new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
function configuredFetch(base: string) {
  return vi.fn((url: string) =>
    Promise.resolve(
      json(url.endsWith("/v1/review-session") ? session : { privacy_bridge_base: base }),
    ),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
  sessionStorage.clear();
  window.localStorage.clear();
});

it("opens the separately authenticated privacy route from trusted public configuration", async () => {
  const fetcher = configuredFetch("http://127.0.0.1:8766");
  vi.stubGlobal("fetch", fetcher);
  window.history.replaceState({}, "", "/privacy?privacy_bridge_base=http://example.com");
  render(<App />);
  expect(await screen.findByLabelText("Local bridge session token")).toBeInTheDocument();
  expect(screen.queryByLabelText("Session token", { exact: true })).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledWith("/local-config.json", {
    credentials: "omit",
    redirect: "error",
    cache: "no-store",
    signal: expect.any(AbortSignal) as AbortSignal,
  });
});

it.each([
  "https://127.0.0.1:8766",
  "http://example.com",
  "http://localhost:8766",
  "http://127.0.0.1:8766/private",
  "http://127.0.0.1:8766/?token=value",
  "http://user:password@127.0.0.1:8766",
  "http://127.0.0.1:8766/#fragment",
])("blocks unapproved privacy configuration %s", async (base) => {
  vi.stubGlobal("fetch", configuredFetch(base));
  window.history.replaceState({}, "", "/privacy");
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Local privacy review is unavailable");
  expect(screen.queryByLabelText("Local bridge session token")).not.toBeInTheDocument();
});

it("guards privacy before any pairing configuration is read", () => {
  sessionStorage.clear();
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  window.history.replaceState({}, "", "/privacy");
  render(<App />);
  expect(screen.getByLabelText("Session token", { exact: true })).toBeVisible();
  expect(screen.queryByLabelText("Local bridge session token")).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
});
