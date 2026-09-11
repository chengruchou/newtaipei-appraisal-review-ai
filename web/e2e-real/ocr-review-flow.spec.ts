import { recordPrivacyHttp } from "./privacy-observation";
import { rehearsalGet } from "./privacy-http";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { ServiceResult } from "../src/api/client";
import { completeLocalOcrReview, type OcrFixture } from "./ocr-review-helper";

test.use({ actionTimeout: 15_000 });
test("individual synthetic visual readings resume the original pending case", async ({ page }) => {
  test.setTimeout(1_200_000);
  const fixturePath = process.env["PRIVACY_BROWSER_FIXTURE"];
  const apiPath = process.env["REVIEW_BROWSER_FIXTURE"];
  if (!fixturePath || !apiPath) throw new Error("Configure the original private case manifests.");
  const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as OcrFixture;
  const api = JSON.parse(readFileSync(apiPath, "utf8")) as { session_token: string };
  const finish = recordPrivacyHttp(
    page,
    fixture.bridge_url,
    test.info().outputPath("local-privacy-http.jsonl"),
  );
  try {
    if (!fixture.restore_result_id || !fixture.completed_job_id)
      throw new Error("This focused wrapper requires the original pending case.");
    const resultResponse = await rehearsalGet(
      page.request,
      `/v1/review-jobs/${fixture.completed_job_id}/result`,
      { headers: { Authorization: `Bearer ${api.session_token}` } },
    );
    expect(resultResponse.ok()).toBe(true);
    const result = (await resultResponse.json()) as ServiceResult;
    expect(result.business_status).toBe("completed");
    expect(result.artifacts).toHaveLength(1);
    const artifact = result.artifacts[0]!;
    expect(artifact.schema_version).toBe("artifact-manifest-v2");
    if (artifact.schema_version === "artifact-manifest-v2")
      expect(artifact.contexts.length).toBeGreaterThan(1);
    await page.goto(new URL(fixture.app_path, fixture.origin).href);
    await page.getByLabel("Local bridge session token", { exact: true }).fill(fixture.token);
    await page.getByRole("button", { name: "Connect local bridge", exact: true }).click();
    await page
      .getByLabel("Authorized result identifier", { exact: true })
      .fill(fixture.restore_result_id);
    const pending = page.waitForResponse(
      (response) =>
        response.url() ===
          `${fixture.bridge_url}/local-privacy/restore/${fixture.restore_result_id}` &&
        response.request().method() === "POST",
      { timeout: 125_000 },
    );
    await page
      .getByRole("button", { name: "Restore result through local bridge", exact: true })
      .click();
    const finalResponse = await completeLocalOcrReview(
      page,
      fixture,
      await pending,
      artifact.content_hash,
    );
    expect(finalResponse.ok()).toBe(true);
    const final = (await finalResponse.json()) as { manifest: { final_digest: string } };
    const downloaded = page.waitForEvent("download");
    await page.getByRole("link", { name: "Save restored PDF locally", exact: true }).click();
    const download = await downloaded;
    expect(await download.failure()).toBeNull();
    expect(
      createHash("sha256")
        .update(readFileSync(await download.path()))
        .digest("hex"),
    ).toBe(final.manifest.final_digest);
    await download.saveAs(test.info().outputPath("restored-browser.pdf"));
    console.info(
      JSON.stringify({
        checkpoint: "existing_case_resume_download_verified",
        sha256: final.manifest.final_digest,
      }),
    );
  } finally {
    finish();
  }
});
