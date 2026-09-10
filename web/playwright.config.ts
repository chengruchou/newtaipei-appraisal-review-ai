import { defineConfig, devices } from "@playwright/test";

/**
 * #25 asks for verification on mainstream Windows browsers. Chromium and Firefox are run
 * here; Edge shares Chromium's engine, and the checklist in docs covers what a human still
 * has to confirm on Windows itself.
 *
 * The smoke runs against the real production build, not the dev server, so it exercises
 * the artifact that would actually be deployed.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env["CI"]),
  retries: process.env["CI"] ? 1 : 0,
  reporter: process.env["CI"] ? "line" : "list",
  use: {
    baseURL: "http://localhost:4173",
    trace: "off",
    // A screenshot or a trace of a real session would capture case content, so both stay
    // off by default rather than being written into an artifact someone later uploads.
    screenshot: "off",
    video: "off",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
  ],
  webServer: {
    command: "npm run preview -- --port 4173 --strictPort",
    url: "http://localhost:4173",
    reuseExistingServer: !process.env["CI"],
    timeout: 60_000,
  },
});
