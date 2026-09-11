import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { ResponseReceipt, RevisionListView, TaskView } from "../src/api/client";

const fixture = JSON.parse(readFileSync(process.env["REVIEW_BROWSER_FIXTURE"]!, "utf8")) as {
  session_token: string;
  job_id: string;
  tasks: { lost_response: string };
};

test("real committed correction survives 502, 504 and body loss with one receipt", async ({
  page,
}) => {
  const id = fixture.tasks.lost_response;
  const headers = { Authorization: `Bearer ${fixture.session_token}` };
  const bodies: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith(`/review-tasks/${id}/responses`))
      bodies.push(request.postData()!);
  });
  await page.goto(`/tasks/${id}`);
  await page.getByLabel("Session token").fill(fixture.session_token);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Respond", exact: true })).toBeVisible();
  await expect(page.getByLabel("Corrected value", { exact: true })).toBeEnabled();
  await page.getByLabel("Corrected value", { exact: true }).fill("12");
  await page.getByRole("button", { name: "Review and submit" }).click();

  let originalReceipt: ResponseReceipt | undefined;
  for (const status of [502, 504, 0]) {
    const armed = await page.request.post("/__test__/gateway-next-response", {
      headers: { "X-Test-Control": "local-rehearsal" },
      data: { task_id: id, status },
    });
    expect(armed.status()).toBe(204);
    await page.getByRole("button", { name: status === 502 ? "Yes, submit" : "Send again" }).click();
    await expect(page.getByRole("button", { name: "Send again" })).toBeVisible();
    await expect(page.getByLabel("Corrected value", { exact: true })).toBeDisabled();
    await expect(page.getByRole("radio", { name: "Refuse to confirm" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Review and submit" })).toHaveCount(0);
    const observed = await page.request.get("/__test__/last-fault-receipt", {
      headers: { "X-Test-Control": "local-rehearsal" },
    });
    expect(observed.ok()).toBe(true);
    const committed = (await observed.json()) as ResponseReceipt;
    if (originalReceipt) expect(committed).toEqual(originalReceipt);
    else originalReceipt = committed;
    expect(committed.revision?.revision_id).toBe("r2");
    const taskResponse = await page.request.get(`/v1/review-tasks/${id}`, { headers });
    expect(taskResponse.ok()).toBe(true);
    expect(((await taskResponse.json()) as TaskView).task.state).toBe("answered");
    const history = await page.request.get(`/v1/review-jobs/${fixture.job_id}/revisions`, {
      headers,
    });
    expect(history.ok()).toBe(true);
    const revisions = ((await history.json()) as RevisionListView).revisions;
    expect(revisions.map((r) => r.reference.revision_id)).toEqual(["r1", "r2"]);
    expect(revisions[1]?.changes[0]?.corrected?.value).toMatchObject({ value: 12, unit: "m" });
  }
  const received = page.waitForResponse(
    (r) => r.url().endsWith(`/review-tasks/${id}/responses`) && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Send again" }).click();
  const replay = await received;
  expect(replay.ok()).toBe(true);
  expect(await replay.json()).toEqual(originalReceipt);
  await expect(page.getByRole("status")).toContainText("Response recorded");
  expect(bodies).toHaveLength(4);
  expect(new Set(bodies).size).toBe(1);
  const finalHistory = await page.request.get(`/v1/review-jobs/${fixture.job_id}/revisions`, {
    headers,
  });
  expect(((await finalHistory.json()) as RevisionListView).revisions).toHaveLength(2);
});
