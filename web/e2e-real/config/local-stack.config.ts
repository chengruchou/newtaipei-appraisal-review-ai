import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";
import { privacyScenarioConfig } from "./scenarios";

const scenario = process.env["LOCAL_STACK_PRIVACY_SCENARIO"];
if (
  scenario !== "positive" &&
  scenario !== "paused" &&
  scenario !== "resume" &&
  scenario !== "automatic"
)
  throw new Error("Select the explicit local stack privacy scenario.");

// Keep shared namespace, freshness, readings, output and test-selection guards.
const config = privacyScenarioConfig(scenario);
delete config.webServer;

export default defineConfig({
  ...config,
  globalSetup: resolve(dirname(fileURLToPath(import.meta.url)), "local-stack-ready.ts"),
});
