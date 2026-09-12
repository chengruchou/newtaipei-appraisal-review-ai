import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, type APIRequestContext } from "@playwright/test";
import type { OcrReviewView } from "../src/privacy/ocr-review-client";
import type { OcrFixture } from "./ocr-review-helper";
import { localPrivacyGet, localPrivacyPost } from "./privacy-http";
import { responseDiagnostic, type PrivacyDiagnostic } from "../src/privacy/diagnostics";

/** Run against the full positive flow's pending stage before any reading plan is supplied. */
export async function assertAutomaticOcrRefusal(request: APIRequestContext) {
  const fixturePath = process.env["PRIVACY_BROWSER_FIXTURE"];
  const directory = process.env["PRIVACY_OCR_READING_DIRECTORY"];
  if (!fixturePath || !directory)
    throw new Error("Configure the pending original full-flow namespace.");
  const root = resolve(directory);
  expect(existsSync(join(root, "published-readings.json"))).toBe(false);
  expect(existsSync(join(root, "restored-readings.json"))).toBe(false);
  const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as OcrFixture;
  const before = JSON.parse(
    readFileSync(join(root, "published-before.json"), "utf8"),
  ) as OcrReviewView;
  const initial = JSON.parse(readFileSync(join(root, "initial-restore-refusal.json"), "utf8")) as {
    status: number;
    request_id: string;
    diagnostic: PrivacyDiagnostic;
    body: { code: string; review_id: string };
  };
  const ready = JSON.parse(readFileSync(join(root, "published-ready.json"), "utf8")) as {
    purpose: string;
    review_id: string;
    result_id: string;
    job_id: string;
    stage: string;
    review_digest: string;
    input_sha256: string;
    observation_count: number;
    item_ids: string[];
    page_sha256: string[];
  };
  expect(ready).toEqual({
    purpose: "exact_synthetic_stage_captured",
    review_id: before.review_id,
    result_id: fixture.restore_result_id,
    job_id: fixture.completed_job_id,
    stage: "published",
    review_digest: before.review_digest,
    input_sha256: before.input_sha256,
    observation_count: before.observations.length,
    item_ids: before.items.map((item) => item.item_id),
    page_sha256: before.pages.map((entry) => entry.image_sha256),
  });
  for (const page of before.pages)
    expect(
      createHash("sha256")
        .update(readFileSync(join(root, `published-page-${page.number}.png`)))
        .digest("hex"),
    ).toBe(page.image_sha256);
  for (const item of before.items)
    expect(existsSync(join(root, `published-item-${item.item_id}.png`))).toBe(true);
  expect(initial.status).toBe(409);
  expect(initial.body).toEqual({
    code: "local_privacy_review_required",
    review_id: before.review_id,
  });
  expect(initial.request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(initial.diagnostic).toEqual({
    httpStatus: 409,
    requestId: initial.request_id,
    serverFailure: { stage: "ocr_validation", code: "privacy_verification_failed" },
    transportFailure: null,
  });
  expect(before.stage).toBe("published");
  expect(before.receipts).toEqual([]);
  expect(before.items.length).toBeGreaterThan(0);
  expect(before.items.every((item) => item.confirmed_reading === null)).toBe(true);
  expect(before.observations.some((obs) => obs.confidence === null || obs.confidence < 0.85)).toBe(
    true,
  );
  const prefix = `${new URL(fixture.bridge_url).origin}/local-privacy`;
  const headers = { Authorization: `Bearer ${fixture.token}`, Origin: fixture.origin };
  const started = performance.now();
  const refusal = await localPrivacyPost(
    request,
    `${prefix}/restore/${fixture.restore_result_id}`,
    headers,
  );
  expect(refusal.status()).toBe(409);
  const repeatElapsedMs = Math.round((performance.now() - started) * 1000) / 1000;
  const repeatDiagnostic = responseDiagnostic(refusal.status(), new Headers(refusal.headers()));
  expect(repeatDiagnostic.requestId).not.toBeNull();
  expect(repeatDiagnostic.requestId).not.toBe(initial.request_id);
  expect(repeatDiagnostic.serverFailure).toBeNull();
  expect(refusal.headers()["x-privacy-failure-stage"]).toBeUndefined();
  expect(refusal.headers()["x-privacy-failure-code"]).toBeUndefined();
  expect(await refusal.json()).toEqual(initial.body);
  const response = await localPrivacyGet(
    request,
    `${prefix}/restore-reviews/${before.review_id}`,
    headers,
  );
  expect(response.ok()).toBe(true);
  const after = (await response.json()) as OcrReviewView;
  expect(after).toEqual(before);
  expect(existsSync(join(root, "published-readings.json"))).toBe(false);
  expect(existsSync(join(root, "restored-readings.json"))).toBe(false);
  const namespace = resolve(root, "..");
  const diagnostic = readdirSync(namespace)
    .filter((name) => name.startsWith("restore-diagnostic-") && name.endsWith(".json"))
    .map((name) => ({ name, bytes: readFileSync(join(namespace, name)) }))
    .find(
      ({ bytes }) =>
        (JSON.parse(bytes.toString("utf8")) as { request_id: string }).request_id ===
        initial.request_id,
    );
  expect(diagnostic).toBeDefined();
  const details = JSON.parse(diagnostic!.bytes.toString("utf8")) as {
    status: number;
    stage: string;
    reason: string;
    observation_count: number;
    low_confidence_count: number;
  };
  expect(details.status).toBe(409);
  expect(details.stage).toBe("published");
  expect(details.reason).toBe("uncertain_ocr");
  expect(details.observation_count).toBeGreaterThan(0);
  expect(details.low_confidence_count).toBeGreaterThan(0);
  const evidence = {
    purpose: "automatic_ocr_refusal_without_visual_confirmations",
    status: refusal.status(),
    initial_request_id: initial.request_id,
    repeat_request_id: refusal.headers()["x-privacy-request-id"],
    repeat_elapsed_ms: repeatElapsedMs,
    initial_diagnostic: initial.diagnostic,
    repeat_diagnostic: repeatDiagnostic,
    review_id: before.review_id,
    input_sha256: before.input_sha256,
    measurements_sha256: createHash("sha256")
      .update(JSON.stringify(before.observations))
      .digest("hex"),
    diagnostic_sha256: createHash("sha256").update(diagnostic!.bytes).digest("hex"),
    observation_count: before.observations.length,
    confirmation_count: 0,
    receipt_count: 0,
  };
  writeFileSync(
    join(root, "automatic-refusal-regression.json"),
    JSON.stringify(evidence, null, 2),
    { mode: 0o600 },
  );
  console.info(JSON.stringify(evidence));
}
