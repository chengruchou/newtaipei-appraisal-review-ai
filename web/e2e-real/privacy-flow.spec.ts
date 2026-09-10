import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import type { ServiceResult, TaskListView, TaskSubjectView } from "../src/api/client";

interface PrivacyFixture {
  bridge_url: string;
  origin: string;
  token: string;
  source_ids: string[];
  review_job_id?: string;
  completed_job_id?: string;
  restore_result_id?: string;
  app_path: string;
  add_region: { page: number; bbox: [number, number, number, number]; category: string };
  sources: { source_id: string; add_region: PrivacyFixture["add_region"] }[];
}
interface ReviewProjection {
  command: {
    source: { case_id: string; snapshot_id: string; pages: { number: number }[] };
    selection_revision: number;
    reviewed_pages: number[];
  };
}

test.use({ actionTimeout: 15_000 });

function observeHttp(page: Page) {
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol === "http:")
      console.info(
        JSON.stringify({ event: "request", method: request.method(), path: url.pathname }),
      );
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.protocol === "http:")
      console.info(
        JSON.stringify({ event: "response", status: response.status(), path: url.pathname }),
      );
  });
  page.on("requestfailed", (request) => {
    console.info(
      JSON.stringify({
        event: "requestfailed",
        path: new URL(request.url()).pathname,
        error: request.failure()?.errorText,
      }),
    );
  });
}

test("real local privacy review explicitly confirms, transfers and restores exact bytes", async ({
  page,
}) => {
  test.setTimeout(240_000);
  observeHttp(page);
  const path = process.env["PRIVACY_BROWSER_FIXTURE"];
  if (!path) throw new Error("PRIVACY_BROWSER_FIXTURE must name the private real bridge manifest");
  const fixture = JSON.parse(readFileSync(path, "utf8")) as PrivacyFixture;
  const reviewFixturePath = process.env["REVIEW_BROWSER_FIXTURE"];
  if (!reviewFixturePath)
    throw new Error("REVIEW_BROWSER_FIXTURE must name the real review API manifest");
  const reviewFixture = JSON.parse(readFileSync(reviewFixturePath, "utf8")) as {
    session_token: string;
  };
  const backendHeaders = { Authorization: `Bearer ${reviewFixture.session_token}` };
  const bridge = new URL(fixture.bridge_url);
  expect(["localhost", "127.0.0.1", "[::1]"]).toContain(bridge.hostname);
  expect(bridge.protocol).toBe("http:");
  expect(fixture.source_ids.length).toBeGreaterThan(0);
  const prefix = `${bridge.origin}/local-privacy`;
  const requests: { method: string; path: string }[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith(`${prefix}/`))
      requests.push({ method: request.method(), path: new URL(request.url()).pathname });
  });
  const count = (suffix: string) =>
    requests.filter((r) => r.method === "POST" && r.path.endsWith(suffix)).length;
  async function clickResponse(name: string, suffix: string) {
    const pending = page.waitForResponse(
      (response) =>
        response.url() === `${prefix}${suffix}` && response.request().method() === "POST",
    );
    await page.getByRole("button", { name, exact: true }).click();
    const response = await pending;
    expect(response.ok()).toBe(true);
    return response;
  }
  async function loadedPage(browser: Page, number: number) {
    const image = browser.getByRole("img", { name: `Original source page ${number}`, exact: true });
    await expect(image).toBeVisible();
    await expect
      .poll(() =>
        image.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0),
      )
      .toBe(true);
  }

  await page.goto(new URL(fixture.app_path, fixture.origin).href);
  expect(new URL(page.url()).origin).toBe(fixture.origin);
  await page.getByLabel("Local bridge session token", { exact: true }).fill(fixture.token);
  await page.getByRole("button", { name: "Connect local bridge" }).click();
  expect(fixture.sources).toHaveLength(2);
  for (const [sourceIndex, source] of fixture.sources.entries()) {
    await page
      .getByRole("combobox", { name: "Source handle", exact: true })
      .selectOption(source.source_id);
    const opened = await clickResponse("Open selected source", `/sources/${source.source_id}/open`);
    let current = (await opened.json()) as ReviewProjection;
    let digest = opened.headers()["x-privacy-review-digest"];
    expect(digest).toMatch(/^[a-f0-9]{64}$/);
    await loadedPage(page, 1);
    expect(count("/review/confirm")).toBe(sourceIndex);
    expect(count("/exports/preview")).toBe(sourceIndex);

    await page
      .getByRole("combobox", { name: "Original page", exact: true })
      .selectOption(String(source.add_region.page));
    await loadedPage(page, source.add_region.page);
    for (const [index, label] of ["Left x", "Bottom y", "Right x", "Top y"].entries())
      await page.getByLabel(label, { exact: true }).fill(String(source.add_region.bbox[index]));
    await page
      .getByRole("combobox", { name: "Region category", exact: true })
      .selectOption(source.add_region.category);
    const added = await clickResponse("Add redaction region", "/review/add");
    expect(added.request().postDataJSON()).toMatchObject({
      revision: current.command.selection_revision,
      region: { page: source.add_region.page, bbox: source.add_region.bbox },
      category: source.add_region.category,
    });
    current = (await added.json()) as ReviewProjection;
    for (const sourcePage of current.command.source.pages) {
      await page
        .getByRole("combobox", { name: "Original page", exact: true })
        .selectOption(String(sourcePage.number));
      await loadedPage(page, sourcePage.number);
      const reviewed = await clickResponse(
        "I reviewed this page and its marked regions",
        "/review/pages",
      );
      current = (await reviewed.json()) as ReviewProjection;
      digest = reviewed.headers()["x-privacy-review-digest"];
      expect(current.command.reviewed_pages).toContain(sourcePage.number);
    }
    expect(count("/review/confirm")).toBe(sourceIndex);
    const confirmedReview = await clickResponse(
      "Confirm reviewed source and regions",
      "/review/confirm",
    );
    expect(confirmedReview.request().postDataJSON()).toEqual({
      schema_version: "local-privacy-v1",
      case_id: current.command.source.case_id,
      snapshot_id: current.command.source.snapshot_id,
      revision: current.command.selection_revision,
      review_digest: digest,
    });

    const pdfResponse = page.waitForResponse(
      (response) =>
        response.url().startsWith(`${prefix}/exports/`) && response.url().endsWith("/pdf"),
    );
    const prepared = await clickResponse("Prepare exact sanitized preview", "/exports/preview");
    expect(prepared.request().postDataJSON()).toEqual({});
    const preview = (await prepared.json()) as {
      preview_id: string;
      payload_digest: string;
      reviewer_text: null;
      manifest: { sanitized_digest: string };
    };
    expect(preview.reviewer_text).toBeNull();
    const pdf = await pdfResponse;
    expect(pdf.ok()).toBe(true);
    expect(
      createHash("sha256")
        .update(await pdf.body())
        .digest("hex"),
    ).toBe(preview.manifest.sanitized_digest);
    await expect(page.getByTitle("Sanitized PDF preview")).toBeVisible();
    const confirmButton = page.getByRole("button", {
      name: "Confirm this exact sanitized payload",
    });
    await expect(confirmButton).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Transfer confirmed payload once" }),
    ).toBeDisabled();
    expect(count(`/exports/${preview.preview_id}/confirm`)).toBe(0);
    expect(count(`/exports/${preview.preview_id}/transfer`)).toBe(0);
    await page.getByRole("checkbox", { name: /I inspected this exact PDF/ }).check();
    const exportConfirmed = await clickResponse(
      "Confirm this exact sanitized payload",
      `/exports/${preview.preview_id}/confirm`,
    );
    expect(exportConfirmed.request().postDataJSON()).toEqual({
      payload_digest: preview.payload_digest,
    });
    expect(count(`/exports/${preview.preview_id}/transfer`)).toBe(0);
    await clickResponse(
      "Transfer confirmed payload once",
      `/exports/${preview.preview_id}/transfer`,
    );
    await expect(page.getByRole("status")).toContainText(
      "this exact sanitized payload was transferred",
    );
    await expect(
      page.getByRole("button", { name: "Transfer confirmed payload once" }),
    ).toBeDisabled();
    expect(count(`/exports/${preview.preview_id}/transfer`)).toBe(1);
  }

  let handoff = fixture;
  await expect
    .poll(
      () => {
        handoff = JSON.parse(readFileSync(path, "utf8")) as PrivacyFixture;
        return Boolean(handoff.review_job_id);
      },
      { timeout: 60_000, intervals: [500, 1000] },
    )
    .toBe(true);
  const jobId = handoff.review_job_id!;
  const reviewer = await page.context().newPage();
  observeHttp(reviewer);
  await reviewer.goto(`/jobs/${jobId}`);
  await reviewer.getByLabel("Session token", { exact: true }).fill(reviewFixture.session_token);
  await reviewer.getByRole("button", { name: "Continue", exact: true }).click();
  const confirmedSides = new Set<string>();
  const originalConfidences: (number | null | undefined)[] = [];
  for (let index = 0; index < 4; index += 1) {
    let listed: TaskListView | undefined;
    await expect
      .poll(
        async () => {
          const response = await reviewer.request.get(`/v1/review-jobs/${jobId}/tasks`, {
            headers: backendHeaders,
          });
          expect(response.ok()).toBe(true);
          listed = (await response.json()) as TaskListView;
          return listed.tasks.some(
            ({ task }) =>
              task.state === "open" && task.side && task.allowed_responses.includes("confirm"),
          );
        },
        { timeout: 30_000, intervals: [500, 1000] },
      )
      .toBe(true);
    const task = listed!.tasks.find(
      ({ task }) =>
        task.state === "open" && task.side && task.allowed_responses.includes("confirm"),
    )!.task;
    const side = task.side!;
    const sideKey = JSON.stringify([side.context, side.factor_id, side.side]);
    expect(confirmedSides.has(sideKey)).toBe(false);
    const metadata = await reviewer.request.get(`/v1/review-tasks/${task.task_id}/subject`, {
      headers: backendHeaders,
    });
    expect(metadata.ok()).toBe(true);
    const subject = (await metadata.json()) as TaskSubjectView;
    originalConfidences.push(subject.observation.confidence);
    await reviewer.goto(`/tasks/${task.task_id}`);
    await reviewer.getByRole("radio", { name: "Confirm this observation", exact: true }).check();
    await reviewer.getByRole("button", { name: "Review and submit", exact: true }).click();
    const responsePromise = reviewer.waitForResponse(
      (response) =>
        response.url().endsWith(`/review-tasks/${task.task_id}/responses`) &&
        response.request().method() === "POST",
    );
    await reviewer.getByRole("button", { name: "Yes, submit", exact: true }).click();
    const response = await responsePromise;
    expect(response.ok()).toBe(true);
    expect(response.request().postDataJSON()).toMatchObject({
      task_id: task.task_id,
      expected_version: task.version,
      revision: task.run.revision,
      side_digest: side.input_digest,
      action: "confirm",
      correction: null,
    });
    await expect(reviewer.getByRole("status")).toContainText("Response recorded");
    confirmedSides.add(sideKey);
  }
  expect(confirmedSides.size).toBe(4);
  expect(originalConfidences).toContain(0);
  await expect
    .poll(
      () => {
        handoff = JSON.parse(readFileSync(path, "utf8")) as PrivacyFixture;
        return Boolean(handoff.completed_job_id && handoff.restore_result_id);
      },
      { timeout: 60_000, intervals: [500, 1000] },
    )
    .toBe(true);
  expect(handoff.completed_job_id).toBe(jobId);
  const resultResponse = await reviewer.request.get(`/v1/review-jobs/${jobId}/result`, {
    headers: backendHeaders,
  });
  expect(resultResponse.ok()).toBe(true);
  const result = (await resultResponse.json()) as ServiceResult;
  expect(result.business_status).toBe("completed");
  expect(result.artifacts).toHaveLength(1);
  const artifact = result.artifacts[0]!;
  expect(artifact.schema_version).toBe("artifact-manifest-v2");
  await reviewer.goto(`/jobs/${jobId}`);
  await reviewer.getByRole("button", { name: "Load current result", exact: true }).click();
  if (artifact.schema_version === "artifact-manifest-v2") {
    expect(artifact.contexts.length).toBeGreaterThan(1);
    const table = reviewer.getByRole("table", { name: "Artifact comparison contexts" });
    await expect(table.locator("tbody tr")).toHaveCount(artifact.contexts.length);
    for (const context of artifact.contexts) {
      await expect(table).toContainText(context.target_id);
      await expect(table).toContainText(context.comparable_id);
    }
  }
  const publishedDownload = reviewer.waitForEvent("download");
  await reviewer.getByRole("button", { name: "Download verified PDF", exact: true }).click();
  const published = await publishedDownload;
  expect(await published.failure()).toBeNull();
  expect(
    createHash("sha256")
      .update(readFileSync(await published.path()))
      .digest("hex"),
  ).toBe(artifact.content_hash);
  await reviewer.close();
  await page
    .getByLabel("Authorized result identifier", { exact: true })
    .fill(handoff.restore_result_id!);
  const restored = await clickResponse(
    "Restore result through local bridge",
    `/restore/${handoff.restore_result_id!}`,
  );
  const restoration = (await restored.json()) as { manifest: { final_digest: string } };
  const downloaded = page.waitForEvent("download");
  await page.getByRole("link", { name: "Save restored PDF locally" }).click();
  const download = await downloaded;
  expect(await download.failure()).toBeNull();
  expect(
    createHash("sha256")
      .update(readFileSync(await download.path()))
      .digest("hex"),
  ).toBe(restoration.manifest.final_digest);
  const unauthorized = await page.request.get(`${prefix}/sources`, {
    headers: { Origin: fixture.origin },
  });
  expect(unauthorized.ok()).toBe(false);
});
