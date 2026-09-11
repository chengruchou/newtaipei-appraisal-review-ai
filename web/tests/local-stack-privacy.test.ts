import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { FullConfig } from "@playwright/test";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import requireHostCompanion, { verifyHostCompanion } from "../e2e-real/config/local-stack-ready";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const bridge = "http://127.0.0.1:8788";
let namespace: string;
let readings: string;
let privacyPath: string;
const closers: (() => Promise<void>)[] = [];

function save(path: string, value: unknown) {
  writeFileSync(path, JSON.stringify(value), { mode: 0o600 });
}
function pending() {
  save(privacyPath, {
    origin: "http://127.0.0.1:4174",
    bridge_url: bridge,
    app_path: "/privacy",
    restore_result_id: "synthetic-result",
    completed_job_id: "synthetic-job",
  });
  mkdirSync(readings, { mode: 0o700 });
  save(join(readings, "published-ready.json"), {});
}
async function configuration(scenario: string) {
  vi.stubEnv("LOCAL_STACK_PRIVACY_SCENARIO", scenario);
  return (await import("../e2e-real/config/local-stack.config")).default;
}
beforeEach(() => {
  vi.resetModules();
  const parent = resolve(webRoot, "../artifacts/browser-repro/stack-config-tests");
  mkdirSync(parent, { recursive: true, mode: 0o700 });
  namespace = mkdtempSync(join(parent, "case-"));
  mkdirSync(join(namespace, "privacy"), { mode: 0o700 });
  mkdirSync(join(namespace, "core"), { mode: 0o700 });
  readings = join(namespace, "readings");
  privacyPath = join(namespace, "privacy/browser-private.json");
  const corePath = join(namespace, "core/fixture.json");
  save(privacyPath, {
    origin: "http://127.0.0.1:4174",
    bridge_url: bridge,
    app_path: "/privacy",
  });
  save(corePath, { api_base_url: "http://127.0.0.1:8766" });
  vi.stubEnv("PRIVACY_BROWSER_FIXTURE", privacyPath);
  vi.stubEnv("REVIEW_BROWSER_FIXTURE", corePath);
  vi.stubEnv("PRIVACY_OCR_READING_DIRECTORY", readings);
  vi.stubEnv("PRIVACY_BROWSER_OUTPUT_DIRECTORY", "");
  vi.stubEnv("TEST_WORKER_INDEX", "");
});
afterEach(async () => {
  for (const close of closers.splice(0)) await close();
  vi.unstubAllEnvs();
  rmSync(namespace, { recursive: true, force: true });
});

it.each([
  ["positive", "privacy-flow.spec.ts"],
  ["paused", "privacy-paused.spec.ts"],
  ["resume", "ocr-review-flow.spec.ts"],
  ["automatic", "ocr-review-automatic.spec.ts"],
] as const)(
  "attaches %s with all shared scenario settings and no web server",
  async (scenario, spec) => {
    if (scenario === "resume" || scenario === "automatic") pending();
    const config = await configuration(scenario);
    expect(config.testMatch).toBe(spec);
    expect(config.webServer).toBeUndefined();
    expect(config.testDir).toBe(join(webRoot, "e2e-real"));
    expect(config.outputDir).toBe(join(namespace, `browser-${scenario}`));
    expect(config.workers).toBe(1);
    expect(config.retries).toBe(0);
    expect(config.fullyParallel).toBe(false);
    expect(config.use).toEqual({
      baseURL: "http://127.0.0.1:4174",
      trace: "off",
      screenshot: "off",
      video: "off",
    });
    expect(config.globalSetup).toBe(join(webRoot, "e2e-real/config/local-stack-ready.ts"));
    if (scenario === "automatic") expect(config.projects).toBeUndefined();
    else expect(config.projects?.map((project) => project.name)).toEqual(["chromium"]);
  },
);
it.each(["", "unknown", "other"])(
  "requires an explicit supported scenario (%s)",
  async (scenario) => {
    await expect(configuration(scenario)).rejects.toThrow("explicit local stack privacy scenario");
  },
);
it.each(["positive", "paused"])(
  "keeps %s freshness and prior-output protection",
  async (scenario) => {
    mkdirSync(join(namespace, `browser-${scenario}`), { mode: 0o700 });
    save(join(namespace, `browser-${scenario}/prior.json`), { failed: true });
    await expect(configuration(scenario)).rejects.toThrow("new browser output");
    vi.resetModules();
    pending();
    await expect(configuration(scenario)).rejects.toThrow("fresh, unused");
  },
);
it("keeps the exact namespace requirement", async () => {
  vi.stubEnv("PRIVACY_OCR_READING_DIRECTORY", join(namespace, "different/readings"));
  await expect(configuration("positive")).rejects.toThrow("one rehearsal namespace");
});
it.each(["resume", "automatic"])("keeps the %s capture checkpoint", async (scenario) => {
  await expect(configuration(scenario)).rejects.toThrow("page-capture checkpoint");
});
it("keeps automatic refusal before any reading plan", async () => {
  pending();
  save(join(readings, "published-readings.json"), {});
  await expect(configuration("automatic")).rejects.toThrow("before any visual reading plan");
});

interface Reply {
  status: number;
  body: unknown;
  headers?: Record<string, string>;
}
async function service(overrides: Record<string, Reply> = {}) {
  const seen: string[] = [];
  const replies: Record<string, Reply> = {
    "/readyz": { status: 200, body: { status: "ready", mode: "host-companion" } },
    "/local-config.json": { status: 200, body: { privacy_bridge_base: bridge } },
    "/__test__/drop-next-response": { status: 404, body: {} },
    ...overrides,
  };
  const server = createServer((request, response) => {
    seen.push(request.url ?? "");
    const reply = replies[request.url ?? ""] ?? { status: 404, body: {} };
    response.writeHead(reply.status, {
      "content-type": "application/json",
      "x-content-type-options": "nosniff",
      ...reply.headers,
    });
    response.end(typeof reply.body === "string" ? reply.body : JSON.stringify(reply.body));
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  closers.push(
    () =>
      new Promise<void>((done, reject) =>
        server.close((error) => (error ? reject(error) : done())),
      ),
  );
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Local listener unavailable");
  return { origin: `http://127.0.0.1:${address.port}`, seen };
}
it("verifies actual loopback readiness, exact companion and absent recovery controls", async () => {
  const local = await service();
  await verifyHostCompanion(local.origin, bridge);
  expect(local.seen).toEqual(["/readyz", "/local-config.json", "/__test__/drop-next-response"]);
});
it.each([
  { status: 503, body: { status: "unavailable", mode: "host-companion" } },
  { status: 200, body: { status: "ready", mode: "synthetic-demo" } },
  { status: 200, body: { status: "ready", mode: "host-companion", extra: true } },
  { status: 200, body: "invalid private synthetic JSON canary" },
  {
    status: 200,
    body: { status: "ready", mode: "host-companion" },
    headers: { "x-content-type-options": "" },
  },
  { status: 302, body: {}, headers: { location: "/redirect-target" } },
])("refuses noncanonical HTTP readiness without later requests ($status)", async (reply) => {
  const local = await service({ "/readyz": reply });
  await expect(verifyHostCompanion(local.origin, bridge)).rejects.toThrow(
    "The configured real host companion server is not ready; no scenario started.",
  );
  expect(local.seen).toEqual(["/readyz"]);
});
it("refuses another companion before probing recovery routes", async () => {
  const local = await service({
    "/local-config.json": { status: 200, body: { privacy_bridge_base: "http://127.0.0.1:8888" } },
  });
  await expect(verifyHostCompanion(local.origin, bridge)).rejects.toThrow("not ready");
  expect(local.seen).toEqual(["/readyz", "/local-config.json"]);
});
it("refuses an exposed rehearsal control endpoint", async () => {
  const local = await service({ "/__test__/drop-next-response": { status: 200, body: {} } });
  await expect(verifyHostCompanion(local.origin, bridge)).rejects.toThrow("not ready");
});
it("requires a bound browser Origin before any live request", async () => {
  const wrong = { projects: [{ use: { baseURL: "http://127.0.0.1:4175" } }] } as FullConfig;
  await expect(requireHostCompanion(wrong)).rejects.toThrow("exact host companion Origin");
  await expect(verifyHostCompanion("https://example.invalid", bridge)).rejects.toThrow("loopback");
});
it("never exposes malformed private manifest contents", async () => {
  writeFileSync(privacyPath, "private synthetic manifest parse canary", { mode: 0o600 });
  const config = { projects: [{ use: { baseURL: "http://127.0.0.1:4174" } }] } as FullConfig;
  await expect(requireHostCompanion(config)).rejects.toThrow(
    "The validated private fixture is unavailable.",
  );
});

it("refuses malformed companion configuration without disclosing its input", async () => {
  const local = await service();
  await expect(
    verifyHostCompanion(local.origin, "private synthetic malformed URL canary"),
  ).rejects.toThrow(/^An exact numeric loopback Origin is required\.$/);
  expect(local.seen).toEqual([]);
});
