import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, type Page, type Response } from "@playwright/test";
import { localPrivacyGet } from "./privacy-http";
import { assertAutomaticOcrRefusal } from "./ocr-review-refusal";
import { validateReadingPlan } from "./ocr-reading-plan";
import { responseDiagnostic } from "../src/privacy/diagnostics";
import type { OcrReviewView, OcrReceipt } from "../src/privacy/ocr-review-client";

export interface OcrFixture {
  bridge_url: string;
  origin: string;
  token: string;
  app_path: string;
  restore_result_id: string;
  completed_job_id: string;
}
const sha = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");

export { localPrivacyGet, localPrivacyPost } from "./privacy-http";

export async function requiresOcrReview(response: Response): Promise<boolean> {
  if (response.status() !== 409) return false;
  const body: unknown = await response.json().catch(() => null);
  return (
    body !== null &&
    typeof body === "object" &&
    !Array.isArray(body) &&
    Object.keys(body).length === 2 &&
    "code" in body &&
    body.code === "local_privacy_review_required" &&
    "review_id" in body &&
    typeof body.review_id === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(body.review_id)
  );
}

function readingRoot() {
  const directory = process.env["PRIVACY_OCR_READING_DIRECTORY"];
  if (!directory)
    throw new Error(
      "Configure an ignored private directory for explicit synthetic visual reading plans.",
    );
  const root = resolve(directory);
  mkdirSync(root, { recursive: true, mode: 0o700 });
  return root;
}

export async function captureLocalOcrReview(
  page: Page,
  fixture: OcrFixture,
  initial: Response,
  publishedHash: string,
  requireFreshStage = false,
): Promise<OcrReviewView> {
  const root = readingRoot();
  const prefix = `${new URL(fixture.bridge_url).origin}/local-privacy`;
  const headers = { Authorization: `Bearer ${fixture.token}`, Origin: fixture.origin };
  const saved = (name: string, value: unknown) =>
    writeFileSync(join(root, name), JSON.stringify(value, null, 2), { mode: 0o600 });
  expect(await requiresOcrReview(initial)).toBe(true);
  const body = (await initial.json()) as { review_id: string };
  const reviewId = body.review_id;
  const diagnostic = responseDiagnostic(initial.status(), new Headers(initial.headers()));
  if (requireFreshStage) {
    expect(diagnostic.httpStatus).toBe(409);
    expect(diagnostic.requestId).not.toBeNull();
    expect(diagnostic.serverFailure).toEqual({
      stage: "ocr_validation",
      code: "privacy_verification_failed",
    });
  }
  const initialPath = join(root, "initial-restore-refusal.json");
  if (!existsSync(initialPath))
    saved("initial-restore-refusal.json", {
      status: initial.status(),
      request_id: initial.headers()["x-privacy-request-id"],
      diagnostic,
      body,
    });
  const restoreButton = page.getByRole("button", {
    name: "Restore result through local bridge",
    exact: true,
  });
  const response = await localPrivacyGet(
    page.request,
    `${prefix}/restore-reviews/${reviewId}`,
    headers,
  );
  expect(response.ok()).toBe(true);
  const view = (await response.json()) as OcrReviewView;
  if (requireFreshStage) {
    expect(view.stage).toBe("published");
    expect(view.receipts).toEqual([]);
    expect(view.items.every((item) => item.confirmed_reading === null)).toBe(true);
  }
  if (view.stage === "published") expect(view.input_sha256).toBe(publishedHash);
  const priorPath = join(root, `${view.stage}-before.json`);
  if (existsSync(priorPath)) {
    const prior = JSON.parse(readFileSync(priorPath, "utf8")) as OcrReviewView;
    expect(view.observations).toEqual(prior.observations);
    expect(view.pages).toEqual(prior.pages);
    expect(view.review_digest).toBe(prior.review_digest);
    expect(view.input_sha256).toBe(prior.input_sha256);
    expect(view.engine_digest).toBe(prior.engine_digest);
  } else saved(`${view.stage}-before.json`, view);
  expect(view.observations.length).toBeGreaterThan(0);
  await expect(
    page.getByRole("heading", {
      name:
        view.stage === "published"
          ? "Review published document OCR"
          : "Review restored candidate OCR",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Save restored PDF locally" })).toHaveCount(0);
  if (view.items.some((item) => item.confirmed_reading === null))
    await expect(restoreButton).toBeDisabled();
  const orderedItems = [...view.items].sort((left, right) => left.page - right.page);
  const pageBytes = new Map<number, Buffer>();
  for (const item of orderedItems) {
    await page.getByLabel("OCR review page", { exact: true }).selectOption(String(item.page));
    await page.getByLabel("Required OCR item", { exact: true }).selectOption(item.item_id);
    const image = page.getByRole("img", {
      name: `OCR review full page ${item.page}`,
      exact: true,
    });
    await expect
      .poll(
        () =>
          image.evaluate(
            (element: HTMLImageElement) => element.complete && element.naturalWidth > 0,
          ),
        { timeout: 30_000 },
      )
      .toBe(true);
    if (!pageBytes.has(item.page)) {
      let captured: number[];
      try {
        captured = await image.evaluate(async (element: HTMLImageElement) => {
          if (!element.currentSrc.startsWith("blob:"))
            throw new Error("Expected the verified local page blob.");
          const response = await fetch(element.currentSrc);
          if (!response.ok) throw new Error("The displayed local page bytes are unavailable.");
          return Array.from(new Uint8Array(await response.arrayBuffer()));
        });
      } catch {
        throw new Error("The exact displayed local page bytes could not be read.");
      }
      pageBytes.set(item.page, Buffer.from(captured));
    }
    const bytes = pageBytes.get(item.page)!;
    expect(sha(bytes)).toBe(view.pages.find((entry) => entry.number === item.page)!.image_sha256);
    writeFileSync(join(root, `${view.stage}-page-${item.page}.png`), bytes, { mode: 0o600 });
    await page
      .getByRole("img", { name: `OCR review region crop ${item.item_id}`, exact: true })
      .screenshot({ path: join(root, `${view.stage}-item-${item.item_id}.png`) });
    if (item.confirmed_reading === null) {
      await expect(page.getByLabel("Exact visible reading", { exact: true })).toHaveValue("");
      await expect(
        page.getByRole("button", { name: "Confirm this individual reading", exact: true }),
      ).toBeDisabled();
    }
  }
  saved(`${view.stage}-ready.json`, {
    purpose: "exact_synthetic_stage_captured",
    review_id: view.review_id,
    stage: view.stage,
    result_id: fixture.restore_result_id,
    job_id: fixture.completed_job_id,
    review_digest: view.review_digest,
    input_sha256: view.input_sha256,
    observation_count: view.observations.length,
    item_ids: view.items.map((item) => item.item_id),
    page_sha256: view.pages.map((entry) => entry.image_sha256),
  });
  console.info(
    JSON.stringify({
      checkpoint: "exact_synthetic_stage_ready_for_individual_readings",
      stage: view.stage,
      item_count: view.items.length,
      observation_count: view.observations.length,
      input_sha256: view.input_sha256,
    }),
  );
  return view;
}

export async function pauseLocalOcrReview(
  page: Page,
  fixture: OcrFixture,
  initial: Response,
  publishedHash: string,
) {
  const view = await captureLocalOcrReview(page, fixture, initial, publishedHash, true);
  expect(view.stage).toBe("published");
  expect(view.observations).toHaveLength(99);
  expect(view.items).toHaveLength(7);
  await assertAutomaticOcrRefusal(page.request);
  console.info(
    JSON.stringify({
      checkpoint: "original_case_paused_without_readings",
      stage: view.stage,
      observation_count: view.observations.length,
      confirmation_count: 0,
      receipt_count: 0,
    }),
  );
}

export async function completeLocalOcrReview(
  page: Page,
  fixture: OcrFixture,
  initial: Response,
  publishedHash: string,
  requireFreshStages = false,
): Promise<Response> {
  const root = readingRoot();
  const prefix = `${new URL(fixture.bridge_url).origin}/local-privacy`;
  const saved = (name: string, value: unknown) =>
    writeFileSync(join(root, name), JSON.stringify(value, null, 2), { mode: 0o600 });
  const restoreButton = page.getByRole("button", {
    name: "Restore result through local bridge",
    exact: true,
  });
  const restorePath = `${prefix}/restore/${fixture.restore_result_id}`;
  async function restore() {
    const pending = page.waitForResponse(
      (response) => response.url() === restorePath && response.request().method() === "POST",
      { timeout: 125_000 },
    );
    await restoreButton.click();
    const response = await pending;
    const body: unknown = await response.json();
    saved(`restore-${Date.now()}.json`, {
      status: response.status(),
      request_id: response.headers()["x-privacy-request-id"],
      body,
    });
    return { response, body };
  }
  let outcome = { response: initial, body: (await initial.json()) as unknown };
  const stages: string[] = [];
  for (let stageNumber = 0; stageNumber < 2 && outcome.response.status() === 409; stageNumber++) {
    expect(await requiresOcrReview(outcome.response)).toBe(true);
    let view = await captureLocalOcrReview(
      page,
      fixture,
      outcome.response,
      publishedHash,
      requireFreshStages && stageNumber === 0,
    );
    if (requireFreshStages) {
      expect(view.observations).toHaveLength(view.stage === "published" ? 99 : 108);
      expect(view.items).toHaveLength(view.stage === "published" ? 7 : 4);
    }
    expect(stages).not.toContain(view.stage);
    stages.push(view.stage);
    const original = structuredClone(view);
    const orderedItems = [...view.items].sort((left, right) => left.page - right.page);
    const planPath = join(root, `${view.stage}-readings.json`);
    await expect
      .poll(() => existsSync(planPath), { timeout: 480_000, intervals: [500] })
      .toBe(true);
    let planValue: unknown;
    try {
      planValue = JSON.parse(readFileSync(planPath, "utf8"));
    } catch {
      throw new Error("The explicit local reading plan could not be parsed.");
    }
    const plan = validateReadingPlan(planValue, view);
    expect(plan.purpose).toBe("automated_synthetic_ui_workflow");
    expect(plan.stage).toBe(view.stage);
    expect(plan.review_digest).toBe(view.review_digest);
    expect(plan.input_sha256).toBe(view.input_sha256);
    expect(plan.readings.map((reading) => reading.item_id).sort()).toEqual(
      view.items.map((item) => item.item_id).sort(),
    );
    for (const item of orderedItems) {
      const reading = plan.readings.find((entry) => entry.item_id === item.item_id)!;
      expect(reading.page_image_sha256).toBe(
        view.pages.find((entry) => entry.number === item.page)!.image_sha256,
      );
      if (item.confirmed_reading !== null) {
        expect(reading.reading).toBe(item.confirmed_reading);
        continue;
      }
      await page.getByLabel("OCR review page", { exact: true }).selectOption(String(item.page));
      await page.getByLabel("Required OCR item", { exact: true }).selectOption(item.item_id);
      const image = page.getByRole("img", {
        name: `OCR review full page ${item.page}`,
        exact: true,
      });
      await expect
        .poll(
          () =>
            image.evaluate(
              (element: HTMLImageElement) => element.complete && element.naturalWidth > 0,
            ),
          { timeout: 30_000 },
        )
        .toBe(true);
      await page.getByLabel("Exact visible reading", { exact: true }).fill(reading.reading);
      await page
        .getByRole("checkbox", {
          name: "I inspected this page and region and transcribed this item exactly.",
          exact: true,
        })
        .check();
      const pending = page.waitForResponse(
        (reply) =>
          reply.url() ===
            `${prefix}/restore-reviews/${view.review_id}/items/${item.item_id}/confirm` &&
          reply.request().method() === "POST",
        { timeout: 35_000 },
      );
      await page
        .getByRole("button", { name: "Confirm this individual reading", exact: true })
        .click();
      const confirmed = await pending;
      expect(confirmed.request().postDataJSON()).toEqual({
        review_digest: view.review_digest,
        page_image_sha256: reading.page_image_sha256,
        reading: reading.reading,
      });
      expect(confirmed.ok()).toBe(true);
      const updated = (await confirmed.json()) as OcrReviewView;
      expect(updated.observations).toEqual(original.observations);
      expect(updated.pages).toEqual(original.pages);
      expect(updated.review_digest).toBe(original.review_digest);
      expect(updated.input_sha256).toBe(original.input_sha256);
      expect(updated.engine_digest).toBe(original.engine_digest);
      expect(updated.items.find((entry) => entry.item_id === item.item_id)!.confirmed_reading).toBe(
        reading.reading,
      );
      view = updated;
    }
    saved(
      existsSync(join(root, `${view.stage}-after.json`))
        ? `${view.stage}-resume-after-${randomUUID()}.json`
        : `${view.stage}-after.json`,
      view,
    );
    expect(view.items.every((item) => item.confirmed_reading !== null)).toBe(true);
    const receipt = view.receipts.find((entry) => entry.stage === view.stage)!;
    expect(receipt.review_digest).toBe(view.review_digest);
    expect(receipt.readings).toEqual(view.items);
    expect(receipt.business_authority).toBe("unchanged");
    await expect(restoreButton).toBeEnabled();
    outcome = await restore();
  }
  expect(outcome.response.ok()).toBe(true);
  if (requireFreshStages) expect(stages).toEqual(["published", "restored"]);
  const final = outcome.body as {
    manifest: { final_digest: string };
    ocr_review_receipts: OcrReceipt[];
  };
  expect(final.ocr_review_receipts.map((receipt) => receipt.stage).sort()).toEqual([
    "published",
    "restored",
  ]);
  for (const receipt of final.ocr_review_receipts)
    expect(receipt.business_authority).toBe("unchanged");
  saved(
    existsSync(join(root, "workflow-evidence.json"))
      ? `resume-workflow-${randomUUID()}.json`
      : "workflow-evidence.json",
    {
      scenario: requireFreshStages ? "complete_original_flow" : "resume_existing_case",
      purpose: "automated_synthetic_ui_workflow",
      stages_clicked_this_run: stages,
      final_sha256: final.manifest.final_digest,
      receipt_ids: final.ocr_review_receipts.map((receipt) => receipt.receipt_id),
      business_authority: "unchanged",
    },
  );
  console.info(
    JSON.stringify({
      checkpoint: "synthetic_individual_visual_stages_complete",
      stages_clicked_this_run: stages,
      sha256: final.manifest.final_digest,
      receipt_count: final.ocr_review_receipts.length,
      business_authority: "unchanged",
    }),
  );
  return outcome.response;
}
