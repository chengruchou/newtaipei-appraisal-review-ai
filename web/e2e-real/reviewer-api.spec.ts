import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import type {
  HumanResponse,
  ResponseReceipt,
  TaskView,
  RevisionListView,
  ServiceResult,
} from "../src/api/client";

interface Fixture {
  api_base_url: string;
  session_token: string;
  empty_job_id: string;
  completed_job_id: string;
  tasks: Record<"confirm" | "correct" | "reject" | "conflict" | "lost_response", string>;
}
const fixture = JSON.parse(readFileSync(process.env["REVIEW_BROWSER_FIXTURE"]!, "utf8")) as Fixture;
const headers = { Authorization: `Bearer ${fixture.session_token}` };

/** These checks read the English wording, so they set the stored language preference the
 * language button writes. No route, response or state is simulated. */
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("workbench.language", "en"));
});

test("the real published result downloads exact authorized artifact bytes", async ({ page }) => {
  await page.goto(`/jobs/${fixture.completed_job_id}/results`);
  await page.getByLabel("Session token").fill(fixture.session_token);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Review overview" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download verified PDF" })).toBeVisible();
  const response = await page.request.get(`/v1/review-jobs/${fixture.completed_job_id}/result`, {
    headers,
  });
  expect(response.ok()).toBe(true);
  const result = (await response.json()) as ServiceResult;
  expect(result.artifacts).toHaveLength(1);
  const artifact = result.artifacts[0]!;
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download verified PDF" }).click();
  const download = await downloaded;
  expect(await download.failure()).toBeNull();
  const bytes = readFileSync(await download.path());
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(artifact.content_hash);
  const refused = await page.request.get(
    `/v1/review-jobs/${fixture.completed_job_id}/artifacts/${artifact.artifact_id}/content`,
  );
  expect(refused.ok()).toBe(false);
});

async function openTask(page: Page, id: string) {
  await page.goto(`/tasks/${id}`);
  await page.getByLabel("Session token").fill(fixture.session_token);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Respond", exact: true })).toBeVisible();
}

test("an authorized empty job still shows its task list and revision history", async ({ page }) => {
  await page.goto(`/jobs/${fixture.empty_job_id}/tasks`);
  await page.getByLabel("Session token").fill(fixture.session_token);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Nothing is waiting for you on this job.")).toBeVisible();
  // The mounted workbench keeps revision history in a disclosure rather than a heading.
  await expect(page.getByText("Revision history")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("the mounted workbench names the official forms without offering a download", async ({
  page,
}) => {
  await page.goto(`/jobs/${fixture.completed_job_id}/forms`);
  await page.getByLabel("Session token").fill(fixture.session_token);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("region", { name: "Form 3 · 地價區段勘查表" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Form 4 · 比較法調查估價表" })).toBeVisible();
  await expect(
    page.getByText("Not available in this deployment: no form download route is published"),
  ).toHaveCount(3);
  await expect(page.getByRole("alert")).toContainText("No official form has been produced.");
});

for (const action of ["confirm", "correct", "reject"] as const) {
  test(`real API records an explicitly reviewed ${action}`, async ({ page }) => {
    const id = fixture.tasks[action];
    await openTask(page, id);
    if (action === "confirm") {
      await page
        .getByRole("button", { name: /Open source page/ })
        .first()
        .click();
      await expect(
        page.getByRole("status").filter({ hasText: "Cited field highlighted" }),
      ).toBeVisible();
      await expect(page.getByTestId("source-field-highlight")).toBeVisible();
      await expect(page.locator("canvas")).toBeVisible();
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
    const result = page.waitForResponse(
      (r) => r.url().endsWith(`/review-tasks/${id}/responses`) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Yes, submit" }).click();
    const response = await result;
    expect(response.ok()).toBe(true);
    const receipt = (await response.json()) as ResponseReceipt;
    expect(receipt.action).toBe(action);
    await expect(page.getByRole("status")).toContainText("Response recorded");
    if (action === "correct") {
      const command = response.request().postDataJSON() as HumanResponse;
      expect(command.correction?.proposed?.value).toMatchObject({
        type: "number",
        value: 12,
        unit: "m",
      });
      const history = await page.request.get(`/v1/review-jobs/${receipt.job.job_id}/revisions`, {
        headers,
      });
      expect(history.ok()).toBe(true);
      const revisions = ((await history.json()) as RevisionListView).revisions;
      expect(revisions.at(-1)?.changes[0]?.corrected?.value).toMatchObject({
        value: 12,
        unit: "m",
      });
    }
  });
}

test("a concurrent real write requires a reload instead of a blind resend", async ({ page }) => {
  const id = fixture.tasks.conflict;
  await openTask(page, id);
  const response = await page.request.get(`/v1/review-tasks/${id}`, { headers });
  const view = (await response.json()) as TaskView;
  await page.getByRole("button", { name: "Review and submit" }).click();
  const rival = await page.request.post(`/v1/review-tasks/${id}/responses`, {
    headers,
    data: {
      schema_version: "service-v1",
      task_id: id,
      expected_version: view.task.version,
      revision: view.task.run.revision,
      side_digest: view.task.side?.input_digest ?? null,
      result_digest: view.task.result_digest ?? null,
      idempotency_key: `rival-${id}`,
      action: "confirm",
      correction: null,
    },
  });
  expect(rival.ok()).toBe(true);
  await page.getByRole("button", { name: "Yes, submit" }).click();
  await expect(page.getByRole("button", { name: "Reload this task" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send again" })).toHaveCount(0);
  await page.getByRole("button", { name: "Reload this task" }).click();
  await expect(page.getByText(/can no longer be answered/)).toBeVisible();
});

test("a response lost after real commit recovers with the identical payload and key", async ({
  page,
}) => {
  const id = fixture.tasks.lost_response;
  await openTask(page, id);
  const bodies: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith(`/review-tasks/${id}/responses`))
      bodies.push(request.postData()!);
  });
  const armed = await page.request.post("/__test__/drop-next-response", {
    headers: { "X-Test-Control": "local-rehearsal" },
    data: { task_id: id },
  });
  expect(armed.status()).toBe(204);
  await page.getByRole("button", { name: "Review and submit" }).click();
  await page.getByRole("button", { name: "Yes, submit" }).click();
  await expect(page.getByRole("button", { name: "Send again" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Refuse to confirm" })).toBeDisabled();
  await page.getByRole("button", { name: "Send again" }).click();
  await expect(page.getByRole("status")).toContainText("Response recorded");
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toBe(bodies[0]);
});
