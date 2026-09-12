import { test } from "@playwright/test";
import { runPrivacyFlow } from "./privacy-flow-helper";

test.use({ actionTimeout: 15_000 });
test("real local privacy review explicitly confirms, transfers and restores exact bytes", async ({
  page,
}) => {
  test.setTimeout(1_200_000);
  await runPrivacyFlow(page, "complete");
});
