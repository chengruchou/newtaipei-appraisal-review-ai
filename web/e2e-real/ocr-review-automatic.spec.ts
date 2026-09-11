import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import type { OcrReviewView } from "../src/privacy/ocr-review-client";
import type { OcrFixture } from "./ocr-review-helper";

/** Run against the full positive flow's pending stage before any reading plan is supplied. */
test("automatic OCR refusal preserves exact measurements and cannot resume without confirmations", async ({
  request,
}) => {
  test.setTimeout(120_000);
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
    body: { code: string; review_id: string };
  };
  expect(initial.status).toBe(409);
  expect(initial.body).toEqual({
    code: "local_privacy_review_required",
    review_id: before.review_id,
  });
  expect(initial.request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(before.stage).toBe("published");
  expect(before.receipts).toEqual([]);
  expect(before.items.length).toBeGreaterThan(0);
  expect(before.items.every((item) => item.confirmed_reading === null)).toBe(true);
  expect(before.observations.some((obs) => obs.confidence === null || obs.confidence < 0.85)).toBe(
    true,
  );
  const prefix = `${new URL(fixture.bridge_url).origin}/local-privacy`;
  const headers = { Authorization: `Bearer ${fixture.token}`, Origin: fixture.origin };
  const refusal = await request.post(`${prefix}/restore/${fixture.restore_result_id}`, {
    headers,
    data: {},
    timeout: 120_000,
  });
  expect(refusal.status()).toBe(409);
  expect(await refusal.json()).toEqual(initial.body);
  const response = await request.get(`${prefix}/restore-reviews/${before.review_id}`, { headers });
  expect(response.ok()).toBe(true);
  const after = (await response.json()) as OcrReviewView;
  expect(after).toEqual(before);
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
});
