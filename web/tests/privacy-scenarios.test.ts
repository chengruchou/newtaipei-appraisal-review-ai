import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { privacyScenarioConfig } from "../e2e-real/config/scenarios";

let root: string;
let namespace: string;
let env: NodeJS.ProcessEnv;
function save(path: string, value: unknown) {
  writeFileSync(path, JSON.stringify(value), { mode: 0o600 });
}
function pending() {
  save(env["PRIVACY_BROWSER_FIXTURE"]!, {
    bridge_url: "http://127.0.0.1:8788",
    origin: "http://127.0.0.1:4174",
    app_path: "/privacy",
    restore_result_id: "synthetic-result",
    completed_job_id: "synthetic-job",
  });
  mkdirSync(env["PRIVACY_OCR_READING_DIRECTORY"]!, { mode: 0o700 });
  save(join(env["PRIVACY_OCR_READING_DIRECTORY"]!, "published-ready.json"), {});
}
beforeEach(() => {
  const parent = resolve("../artifacts/browser-repro/config-tests");
  mkdirSync(parent, { recursive: true, mode: 0o700 });
  root = mkdtempSync(join(parent, "case-"));
  namespace = join(root, "artifacts/session");
  mkdirSync(join(namespace, "privacy"), { recursive: true, mode: 0o700 });
  mkdirSync(join(namespace, "core"), { mode: 0o700 });
  mkdirSync(join(root, "web"), { mode: 0o700 });
  env = {
    PRIVACY_BROWSER_FIXTURE: join(namespace, "privacy/browser-private.json"),
    REVIEW_BROWSER_FIXTURE: join(namespace, "core/fixture.json"),
    PRIVACY_OCR_READING_DIRECTORY: join(namespace, "readings"),
  };
  save(env["PRIVACY_BROWSER_FIXTURE"]!, {
    bridge_url: "http://127.0.0.1:8788",
    origin: "http://127.0.0.1:4174",
    app_path: "/privacy",
  });
  save(env["REVIEW_BROWSER_FIXTURE"]!, { api_base_url: "http://127.0.0.1:8766" });
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

it.each(["positive", "paused"] as const)(
  "selects only the fresh %s scenario and one actual browser server",
  (scenario) => {
    const config = privacyScenarioConfig(scenario, env, join(root, "web"));
    expect(config.testMatch).toBe(
      scenario === "positive" ? "privacy-flow.spec.ts" : "privacy-paused.spec.ts",
    );
    expect(config.workers).toBe(1);
    expect(config.retries).toBe(0);
    expect(config.webServer).toMatchObject({
      command: "node scripts/browser-proxy.mjs",
      reuseExistingServer: false,
    });
    expect(config.use).toMatchObject({ trace: "off", screenshot: "off", video: "off" });
    expect(config.outputDir).toBe(join(namespace, `browser-${scenario}`));
  },
);
it("runs automatic refusal without starting a competing proxy or browser", () => {
  pending();
  const config = privacyScenarioConfig("automatic", env, join(root, "web"));
  expect(config.testMatch).toBe("ocr-review-automatic.spec.ts");
  expect(config.webServer).toBeUndefined();
  expect(config.projects).toBeUndefined();
});
it("selects a separate existing-case resume with its own preserved output", () => {
  pending();
  const config = privacyScenarioConfig("resume", env, join(root, "web"));
  expect(config.testMatch).toBe("ocr-review-flow.spec.ts");
  expect(config.outputDir).toBe(join(namespace, "browser-resume"));
});
it.each(["positive", "paused"] as const)("rejects consumed fixtures for %s", (scenario) => {
  pending();
  expect(() => privacyScenarioConfig(scenario, env, join(root, "web"))).toThrow("fresh, unused");
});
it.each(["automatic", "resume"] as const)(
  "rejects %s before the captured-stage checkpoint",
  (scenario) => {
    expect(() => privacyScenarioConfig(scenario, env, join(root, "web"))).toThrow(
      "page-capture checkpoint",
    );
  },
);
it("refuses automatic execution after a reading plan appears", () => {
  pending();
  save(join(env["PRIVACY_OCR_READING_DIRECTORY"]!, "published-readings.json"), {});
  expect(() => privacyScenarioConfig("automatic", env, join(root, "web"))).toThrow(
    "before any visual reading plan",
  );
});
it("refuses cross-namespace reading evidence", () => {
  env["PRIVACY_OCR_READING_DIRECTORY"] = join(root, "artifacts/another/readings");
  expect(() => privacyScenarioConfig("positive", env, join(root, "web"))).toThrow(
    "one rehearsal namespace",
  );
});
it("keeps an earlier browser output from being erased on retry", () => {
  mkdirSync(join(namespace, "browser-positive"));
  save(join(namespace, "browser-positive/prior-failure.json"), { failed: true });
  expect(() => privacyScenarioConfig("positive", env, join(root, "web"))).toThrow(
    "new browser output",
  );
});
it("permits the runner-created output only when reloading in an actual IPC worker", () => {
  mkdirSync(join(namespace, "browser-positive"));
  save(join(namespace, "browser-positive/runner-output.json"), {});
  env["TEST_WORKER_INDEX"] = "0";
  const descriptor = Object.getOwnPropertyDescriptor(process, "send");
  try {
    Object.defineProperty(process, "send", { configurable: true, value: undefined });
    expect(() => privacyScenarioConfig("positive", env, join(root, "web"))).toThrow(
      "new browser output",
    );
    Object.defineProperty(process, "send", { configurable: true, value: () => true });
    expect(privacyScenarioConfig("positive", env, join(root, "web")).outputDir).toBe(
      join(namespace, "browser-positive"),
    );
    env["PRIVACY_BROWSER_OUTPUT_DIRECTORY"] = join(root, "outside-namespace");
    expect(() => privacyScenarioConfig("positive", env, join(root, "web"))).toThrow(
      "new browser output",
    );
  } finally {
    if (descriptor) Object.defineProperty(process, "send", descriptor);
    else delete process.send;
  }
});
it("refuses reading-directory aliases", () => {
  mkdirSync(join(root, "elsewhere"));
  symlinkSync(join(root, "elsewhere"), env["PRIVACY_OCR_READING_DIRECTORY"]!);
  expect(() => privacyScenarioConfig("positive", env, join(root, "web"))).toThrow(
    "directory alias",
  );
});
it("does not reveal malformed private manifest contents", () => {
  const privateValue = "private synthetic parse canary";
  writeFileSync(env["PRIVACY_BROWSER_FIXTURE"]!, `{${privateValue}`);
  let error: unknown;
  try {
    privacyScenarioConfig("positive", env, join(root, "web"));
  } catch (caught) {
    error = caught;
  }
  expect(error).toBeInstanceOf(Error);
  expect((error as Error).message).not.toContain(privateValue);
  expect((error as Error).cause).toBeUndefined();
});
it("refuses public listener configuration", () => {
  save(env["REVIEW_BROWSER_FIXTURE"]!, { api_base_url: "http://0.0.0.0:8766" });
  expect(() => privacyScenarioConfig("positive", env, join(root, "web"))).toThrow(
    "numeric loopback",
  );
});
