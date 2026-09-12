import { test } from "@playwright/test";
import { assertAutomaticOcrRefusal } from "./ocr-review-refusal";

test("automatic OCR refusal preserves exact measurements and cannot resume without confirmations", async ({
  request,
}) => {
  test.setTimeout(120_000);
  await assertAutomaticOcrRefusal(request);
});
