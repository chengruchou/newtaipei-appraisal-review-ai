import { test } from "@playwright/test";
import { runPrivacyFlow } from "./privacy-flow-helper";

test.use({ actionTimeout: 15_000 });
test("fresh original case pauses at automatic OCR refusal without visual confirmations", async ({
  page,
}) => {
  test.setTimeout(1_200_000);
  await runPrivacyFlow(page, "paused");
});
