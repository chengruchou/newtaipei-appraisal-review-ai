import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

export type PrivacyScenario = "positive" | "paused" | "automatic" | "resume";
const webDirectory = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const specs: Record<PrivacyScenario, string> = {
  positive: "privacy-flow.spec.ts",
  paused: "privacy-paused.spec.ts",
  automatic: "ocr-review-automatic.spec.ts",
  resume: "ocr-review-flow.spec.ts",
};

function requiredPath(env: NodeJS.ProcessEnv, key: string): string {
  const value = env[key];
  if (!value || !isAbsolute(value)) throw new Error(`${key} must be an absolute local path.`);
  return resolve(value);
}
function objectFile(path: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(readFileSync(path, "utf8"));
    if (value && typeof value === "object" && !Array.isArray(value))
      return value as Record<string, unknown>;
  } catch {
    // File contents and parse errors may contain local credentials.
  }
  throw new Error("A readable private rehearsal manifest is required.");
}
function localHttp(value: unknown): string {
  try {
    const url = new URL(typeof value === "string" ? value : "");
    if (
      url.protocol === "http:" &&
      url.hostname === "127.0.0.1" &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      url.pathname === "/"
    )
      return url.origin;
  } catch {
    // Do not expose an invalid URL from a private manifest.
  }
  throw new Error("Rehearsal listeners must use numeric loopback HTTP origins.");
}

export function privacyScenarioConfig(
  scenario: PrivacyScenario,
  env: NodeJS.ProcessEnv = process.env,
  webRoot = webDirectory,
) {
  const privacyPath = requiredPath(env, "PRIVACY_BROWSER_FIXTURE");
  const corePath = requiredPath(env, "REVIEW_BROWSER_FIXTURE");
  const readings = requiredPath(env, "PRIVACY_OCR_READING_DIRECTORY");
  let namespace: string;
  let artifacts: string;
  try {
    namespace = realpathSync(resolve(privacyPath, "../.."));
    artifacts = realpathSync(resolve(webRoot, "../artifacts"));
    if (realpathSync(privacyPath) !== privacyPath || realpathSync(corePath) !== corePath)
      throw new Error("Alias");
  } catch {
    throw new Error("Use an existing, confined rehearsal namespace without file aliases.");
  }
  const inside = relative(artifacts, namespace);
  if (!inside || inside.startsWith(`..${sep}`) || inside === ".." || isAbsolute(inside))
    throw new Error("The rehearsal namespace must be inside this checkout's artifacts directory.");
  if (
    privacyPath !== join(namespace, "privacy/browser-private.json") ||
    corePath !== join(namespace, "core/fixture.json") ||
    dirname(readings) !== namespace
  )
    throw new Error("Core, privacy and reading evidence must belong to one rehearsal namespace.");
  if (existsSync(readings) && realpathSync(readings) !== readings)
    throw new Error("Reading evidence must not use a directory alias.");
  const privacy = objectFile(privacyPath);
  const core = objectFile(corePath);
  const origin = localHttp(privacy.origin);
  localHttp(privacy.bridge_url);
  localHttp(core.api_base_url);
  if (origin !== "http://127.0.0.1:4174" || privacy.app_path !== "/privacy")
    throw new Error("The private fixture must match the configured rehearsal browser origin.");
  const fresh = scenario === "positive" || scenario === "paused";
  if (
    fresh &&
    (privacy.restore_result_id ||
      privacy.completed_job_id ||
      (existsSync(readings) && readdirSync(readings).length > 0))
  )
    throw new Error("A complete or paused original flow requires fresh, unused case evidence.");
  if (
    !fresh &&
    (typeof privacy.restore_result_id !== "string" ||
      typeof privacy.completed_job_id !== "string" ||
      !existsSync(join(readings, "published-ready.json")))
  )
    throw new Error("Use the same published case after its complete page-capture checkpoint.");
  if (
    scenario === "automatic" &&
    ["published", "restored"].some((stage) => existsSync(join(readings, `${stage}-readings.json`)))
  )
    throw new Error("Automatic refusal must run before any visual reading plan exists.");
  const output = env["PRIVACY_BROWSER_OUTPUT_DIRECTORY"]
    ? requiredPath(env, "PRIVACY_BROWSER_OUTPUT_DIRECTORY")
    : join(namespace, `browser-${scenario}`);
  // Playwright reloads this module in an IPC worker after creating its output.
  const workerReload =
    typeof process.send === "function" && /^\d+$/.test(env["TEST_WORKER_INDEX"] ?? "");
  if (
    dirname(output) !== namespace ||
    output === readings ||
    (existsSync(output) &&
      (realpathSync(output) !== output ||
        !statSync(output).isDirectory() ||
        (!workerReload && readdirSync(output).length > 0)))
  )
    throw new Error("Use a new browser output directory directly within the same namespace.");
  return defineConfig({
    testDir: resolve(webRoot, "e2e-real"),
    testMatch: specs[scenario],
    workers: 1,
    fullyParallel: false,
    retries: 0,
    reporter: "list",
    outputDir: output,
    use: { baseURL: origin, trace: "off", screenshot: "off", video: "off" },
    ...(scenario === "automatic"
      ? {}
      : {
          projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
          webServer: {
            command: "node scripts/browser-proxy.mjs",
            cwd: webRoot,
            url: origin,
            reuseExistingServer: false,
          },
        }),
  });
}
