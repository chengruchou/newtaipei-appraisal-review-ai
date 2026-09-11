import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "@/App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
  sessionStorage.clear();
});

it("opens the separately authenticated privacy route from trusted public configuration", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        privacy_bridge_base: "http://127.0.0.1:8766",
      }),
      { headers: { "Content-Type": "application/json" } },
    ),
  );
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
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ privacy_bridge_base: base }))),
  );
  window.history.replaceState({}, "", "/privacy");
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Local privacy review is unavailable");
  expect(screen.queryByLabelText("Local bridge session token")).not.toBeInTheDocument();
});
