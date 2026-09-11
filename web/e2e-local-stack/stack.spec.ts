import { createHash } from "node:crypto";
import { chmodSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { HumanResponse, ResponseReceipt, ServiceResult } from "../src/api/client";

interface Session {
  session_token: string;
  completed_job_id: string;
  tasks: Record<string, string>;
}
const session = JSON.parse(readFileSync(process.env["LOCAL_STACK_SESSION"]!, "utf8")) as Session;
const headers = { Authorization: `Bearer ${session.session_token}` };
const output = process.env["LOCAL_STACK_EVIDENCE"]!;

test("built workbench retrieves and saves the exact authorized published PDF", async ({ page }) => {
  await page.goto(`/jobs/${session.completed_job_id}`);
  await page.getByLabel("Session token").fill(session.session_token);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Load current result" }).click();
  const response = await page.request.get(`/v1/review-jobs/${session.completed_job_id}/result`, {
    headers,
  });
  expect(response.status()).toBe(200);
  const result = (await response.json()) as ServiceResult;
  expect(result.business_status).toBe("completed");
  expect(result.artifacts).toHaveLength(1);
  const artifact = result.artifacts[0]!;
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download verified PDF" }).click();
  const download = await downloaded;
  expect(await download.failure()).toBeNull();
  const path = join(output, "browser-published.pdf");
  await download.saveAs(path);
  chmodSync(path, 0o600);
  expect(createHash("sha256").update(readFileSync(path)).digest("hex")).toBe(artifact.content_hash);
  const route = `/v1/review-jobs/${session.completed_job_id}/artifacts/${artifact.artifact_id}/content`;
  expect((await page.request.get(route)).status()).toBe(403);
  for (const path of ["/session.json", "/fixture.json", "/__test__/drop-next-response"])
    expect((await page.request.get(path)).status()).toBe(404);
  writeFileSync(join(output, "published.json"), JSON.stringify({ result, path: route }), {
    mode: 0o600,
  });
});

for (const action of ["confirm", "correct", "reject"] as const) {
  test(`canonical API records ${action} and returns the identical receipt on replay`, async ({
    page,
  }) => {
    const id = session.tasks[action]!;
    await page.goto(`/tasks/${id}`);
    await page.getByLabel("Session token").fill(session.session_token);
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Respond", exact: true })).toBeVisible();
    if (action === "confirm") {
      await page
        .getByRole("button", { name: /Open source page/ })
        .first()
        .click();
      await expect(page.locator("canvas")).toBeVisible();
      await expect(page.getByTestId("source-field-highlight")).toBeVisible();
      await page.getByRole("button", { name: "Close source page" }).click();
    }
    await page
      .getByRole("radio", {
        name:
          action === "confirm"
            ? "Confirm this observation"
            : action === "correct"
              ? "Submit a correction"
              : "Refuse to confirm",
      })
      .click();
    if (action === "correct") await page.getByLabel("Corrected value", { exact: true }).fill("12");
    await page.getByRole("button", { name: "Review and submit" }).click();
    const received = page.waitForResponse(
      (r) => r.url().endsWith(`/review-tasks/${id}/responses`) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Yes, submit" }).click();
    const response = await received;
    expect(response.status()).toBe(200);
    const receipt = (await response.json()) as ResponseReceipt;
    const command = response.request().postDataJSON() as HumanResponse;
    expect(receipt.action).toBe(action);
    await expect(page.getByRole("status")).toContainText("Response recorded");
    const replay = await page.request.post(`/v1/review-tasks/${id}/responses`, {
      headers,
      data: command,
    });
    expect(replay.status()).toBe(200);
    expect(await replay.json()).toEqual(receipt);
    const denied = await page.request.post(`/v1/review-tasks/${id}/responses`, { data: command });
    expect(denied.status()).toBe(403);
    writeFileSync(join(output, `${action}-receipt.json`), JSON.stringify({ command, receipt }), {
      mode: 0o600,
    });
  });
}
