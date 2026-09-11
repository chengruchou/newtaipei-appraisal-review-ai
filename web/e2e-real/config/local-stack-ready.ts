import { readFileSync } from "node:fs";
import { request, type FullConfig } from "@playwright/test";

function numericOrigin(value: string): void {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "http:" ||
      url.hostname !== "127.0.0.1" ||
      url.origin !== value ||
      !url.port ||
      Number(url.port) < 1024
    )
      throw new Error("Invalid Origin");
  } catch {
    throw new Error("An exact numeric loopback Origin is required.");
  }
}

export async function verifyHostCompanion(origin: string, expectedBridge: string): Promise<void> {
  numericOrigin(origin);
  numericOrigin(expectedBridge);
  const client = await request.newContext({ baseURL: origin, timeout: 5_000 });
  try {
    const ready = await client.get("/readyz", { maxRedirects: 0 });
    if (ready.status() !== 200 || ready.headers()["x-content-type-options"] !== "nosniff")
      throw new Error("Readiness refused.");
    const state: unknown = await ready.json();
    if (
      !state ||
      typeof state !== "object" ||
      Object.keys(state).sort().join(",") !== "mode,status" ||
      !("mode" in state) ||
      state.mode !== "host-companion" ||
      !("status" in state) ||
      state.status !== "ready"
    )
      throw new Error("Unexpected readiness response.");
    const response = await client.get("/local-config.json", { maxRedirects: 0 });
    if (response.status() !== 200) throw new Error("Companion configuration refused.");
    const local: unknown = await response.json();
    if (
      !local ||
      typeof local !== "object" ||
      Object.keys(local).join(",") !== "privacy_bridge_base" ||
      !("privacy_bridge_base" in local) ||
      local.privacy_bridge_base !== expectedBridge
    )
      throw new Error("Companion configuration differs.");
    if ((await client.get("/__test__/drop-next-response", { maxRedirects: 0 })).status() !== 404)
      throw new Error("Unexpected recovery control surface.");
  } catch {
    throw new Error("The configured real host companion server is not ready; no scenario started.");
  } finally {
    await client.dispose();
  }
}

export default async function requireHostCompanion(config: FullConfig): Promise<void> {
  const path = process.env["PRIVACY_BROWSER_FIXTURE"];
  if (!path) throw new Error("The validated private fixture is required.");
  let fixture: unknown;
  try {
    fixture = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new Error("The validated private fixture is unavailable.");
  }
  if (
    !fixture ||
    typeof fixture !== "object" ||
    !("origin" in fixture) ||
    fixture.origin !== "http://127.0.0.1:4174" ||
    !("bridge_url" in fixture) ||
    typeof fixture.bridge_url !== "string" ||
    config.projects.length === 0 ||
    config.projects.some((project) => project.use.baseURL !== fixture.origin)
  )
    throw new Error("The browser must use the exact host companion Origin.");
  await verifyHostCompanion(fixture.origin, fixture.bridge_url);
}
