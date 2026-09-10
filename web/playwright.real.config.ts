import { defineConfig, devices } from "@playwright/test";

if (!process.env["REVIEW_BROWSER_FIXTURE"]) throw new Error("REVIEW_BROWSER_FIXTURE must name an isolated, configured API fixture manifest");

export default defineConfig({
  testDir: "./e2e-real",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  outputDir: "../artifacts/playwright-real",
  use: { baseURL: "http://127.0.0.1:4174", trace: "off", screenshot: "off", video: "off" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: { command: "node scripts/browser-proxy.mjs", url: "http://127.0.0.1:4174", reuseExistingServer: false },
});
