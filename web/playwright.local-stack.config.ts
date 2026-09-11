import { readFileSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

const session = process.env["LOCAL_STACK_SESSION"];
const output = process.env["LOCAL_STACK_EVIDENCE"];
if (!session || !output)
  throw new Error("Explicit private local stack session and output required");
const fixture = JSON.parse(readFileSync(session, "utf8")) as { api_base_url: string };
const origin = new URL(fixture.api_base_url);
if (origin.protocol !== "http:" || origin.hostname !== "127.0.0.1")
  throw new Error("Only the operator's local stack can be exercised");

export default defineConfig({
  testDir: "./e2e-local-stack",
  outputDir: output,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: origin.origin,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(process.env["LOCAL_STACK_CHROME"]
          ? { launchOptions: { executablePath: process.env["LOCAL_STACK_CHROME"] } }
          : {}),
      },
    },
  ],
});
