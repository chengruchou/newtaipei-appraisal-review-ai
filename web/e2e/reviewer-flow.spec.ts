import { expect, test } from "@playwright/test";

/**
 * One real browser walking the reviewer's path: sign in, open a job, read a task and its
 * evidence, submit a confirmation, and see the new revision.
 *
 * The service is stubbed at the network boundary rather than run for real, so this smoke
 * checks the artifact that ships — routing, rendering, focus order and the submit path —
 * without needing AWS or a database. The service's own behaviour is covered by its tests.
 */

const DIGEST = "a".repeat(64);
const JOB = "33333333-3333-3333-3333-333333333333";
const TASK = "11111111-1111-1111-1111-111111111111";
const SUBJECT = '["regional","\\u677f\\u6a4b-A","\\u4e09\\u91cd-B"]:road_width:target';

const revision = {
  schema_version: "service-v1",
  case_id: "case-1",
  revision_id: "r1",
  material_digest: DIGEST,
};

const task = {
  schema_version: "service-v1",
  task_id: TASK,
  run: {
    schema_version: "service-v1",
    run_id: JOB,
    revision,
    attempt_id: null,
    runtime_session_id: null,
  },
  version: 1,
  kind: "fact_confirmation",
  required_permission: "confirm_observation",
  state: "open",
  side: {
    schema_version: "service-v1",
    context: { scope: "regional", target_id: "板橋-A", comparable_id: "三重-B" },
    factor_id: "road_width",
    side: "target",
    input_digest: DIGEST,
  },
  result_digest: null,
  question: "Is the target road width 10 m?",
  evidence: [
    {
      document_id: "forms.pdf",
      content_hash: DIGEST,
      version: "1",
      page: 3,
      region_id: "row-7",
      bbox: [10, 20, 120, 44],
      excerpt: "臨路寬度 10 公尺",
    },
  ],
  finding_ids: ["road_width"],
  allowed_responses: ["confirm", "reject"],
};

test.beforeEach(async ({ page }) => {
  const submissions: unknown[] = [];
  await page.route("**/v1/review-jobs/*/tasks", (route) =>
    route.fulfill({
      json: {
        schema_version: "service-v1",
        job: { schema_version: "service-v1", case_id: "case-1", job_id: JOB },
        tasks: [{ schema_version: "service-v1", task, subject_id: SUBJECT }],
      },
    }),
  );
  await page.route("**/v1/review-jobs/*/revisions", (route) =>
    route.fulfill({
      json: {
        schema_version: "service-v1",
        job: { schema_version: "service-v1", case_id: "case-1", job_id: JOB },
        revisions: [
          {
            schema_version: "service-v1",
            reference: revision,
            parent: null,
            documents: ["forms", "criteria"].map((purpose) => ({
              schema_version: "service-v1",
              case_id: "case-1",
              document_id: `${purpose}.pdf`,
              version: "1",
              content_hash: DIGEST,
              purpose,
            })),
            rules: [
              {
                schema_version: "service-v1",
                rule_set_id: "synthetic-rules",
                version: "1",
                context: task.side.context,
                content_hash: DIGEST,
              },
            ],
            changes: [],
            canonicalization: "review-material-json-v1",
          },
        ],
      },
    }),
  );
  await page.route(/\/v1\/review-jobs\/[^/]+$/, (route) =>
    route.fulfill({
      json: {
        schema_version: "service-v1",
        job: { schema_version: "service-v1", case_id: "case-1", job_id: JOB },
        job_status: "waiting_for_human",
        current_run: null,
        attempt_count: 1,
        result_version: 0,
        cancel_requested: false,
        open_task_ids: [TASK],
        problem: null,
      },
    }),
  );
  await page.route(`**/v1/review-tasks/${TASK}/responses`, async (route) => {
    submissions.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        schema_version: "service-v1",
        task_id: TASK,
        consumed_version: 1,
        task_state: "answered",
        action: "confirm",
        job: { schema_version: "service-v1", case_id: "case-1", job_id: JOB },
        job_status: "queued",
        revision: { ...revision, revision_id: "r2", material_digest: "b".repeat(64) },
        resumed_run: {
          schema_version: "service-v1",
          run_id: "44444444-4444-4444-4444-444444444444",
          revision: { ...revision, revision_id: "r2", material_digest: "b".repeat(64) },
          attempt_id: null,
          runtime_session_id: null,
        },
        superseded_task_ids: [],
      },
    });
  });
  await page.route(`**/v1/review-tasks/${TASK}`, (route) =>
    route.fulfill({ json: { schema_version: "service-v1", task, subject_id: SUBJECT } }),
  );
  await page.exposeFunction("__submissions", () => submissions);
});

test("a reviewer can sign in, read a task with its evidence and confirm it", async ({ page }) => {
  await page.goto("/");

  await page.getByLabel("Session token").fill("smoke-token");
  await page.getByRole("button", { name: "Continue" }).click();

  await page.getByLabel("Job identifier").fill(JOB);
  await page.getByRole("button", { name: "Open" }).click();

  await expect(page.getByRole("heading", { name: "Case case-1" })).toBeVisible();
  await expect(page.getByText("Waiting for a reviewer")).toBeVisible();

  await page.getByRole("link", { name: /road width/i }).click();

  await expect(page.getByRole("heading", { name: "Confirm an observation" })).toBeVisible();
  // The evidence must be on screen before the form, not hidden behind a disclosure.
  await expect(page.getByText("臨路寬度 10 公尺")).toBeVisible();
  await expect(page.getByText("forms.pdf")).toBeVisible();

  await page.getByRole("button", { name: /review and submit/i }).click();
  await page.getByRole("button", { name: /yes, submit/i }).click();

  await expect(page.getByRole("status")).toContainText("Response recorded");
  await expect(page.getByRole("status")).toContainText("r2");
});

test("the whole task page is reachable with the keyboard alone", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Session token").fill("smoke-token");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Job identifier").fill(JOB);
  await page.getByRole("button", { name: "Open" }).click();
  await page.getByRole("link", { name: /road width/i }).click();
  await expect(page.getByRole("heading", { name: "Confirm an observation" })).toBeVisible();

  // Walk forward from the top of the document and require that the submit control is
  // reachable without a pointer, which is what #25's keyboard criterion actually means.
  const reached: string[] = [];
  for (let step = 0; step < 25; step += 1) {
    await page.keyboard.press("Tab");
    const label = await page.evaluate(() => {
      const active = document.activeElement;
      return active === null ? "" : `${active.tagName}:${active.textContent?.trim() ?? ""}`;
    });
    reached.push(label);
    if (label.startsWith("BUTTON") && /review and submit/i.test(label)) {
      break;
    }
  }

  expect(reached.some((entry) => /review and submit/i.test(entry))).toBe(true);
});

test("the confirmation step cannot be skipped by pressing Enter in the form", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Session token").fill("smoke-token");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Job identifier").fill(JOB);
  await page.getByRole("button", { name: "Open" }).click();
  await page.getByRole("link", { name: /road width/i }).click();

  await page.getByRole("radio", { name: /confirm this observation/i }).press("Enter");

  // Enter submits the form, which must land on the confirmation step and send nothing.
  await expect(page.getByRole("button", { name: /yes, submit/i })).toBeVisible();
  const sent = await page.evaluate(() =>
    (window as unknown as { __submissions: () => Promise<unknown[]> }).__submissions(),
  );
  expect(sent).toHaveLength(0);
});
