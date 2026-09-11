import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LocalPrivacyClient } from "@/privacy/client";
import {
  currentOcrReceipt,
  OcrReviewRequired,
  type OcrReviewView,
} from "@/privacy/ocr-review-client";
import { completedView, HASH, ITEM, ocrView, PLACEHOLDER, REVIEW } from "./ocr-review-fixtures";

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
function client(fetch: typeof globalThis.fetch) {
  return new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:9000",
    token: () => "synthetic-local-session",
    fetch,
  });
}
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

it("keeps a bounded longer deadline only for restoration page inspection", async () => {
  vi.useFakeTimers();
  let reply!: (response: Response) => void;
  const fetch = vi.fn<typeof globalThis.fetch>().mockImplementation(
    () =>
      new Promise((resolve) => {
        reply = resolve;
      }),
  );
  const pending = expect(client(fetch).restore(REVIEW)).rejects.toBeInstanceOf(OcrReviewRequired);
  await vi.advanceTimersByTimeAsync(31_000);
  expect(fetch.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
  reply(json({ code: "local_privacy_review_required", review_id: REVIEW }, 409));
  await pending;
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([false, true])(
  "aborts at the original read or restoration deadline without retry: restore=%s",
  async (restore) => {
    vi.useFakeTimers();
    const fetch = vi.fn<typeof globalThis.fetch>().mockImplementation(() => new Promise(() => {}));
    const local = client(fetch);
    const pending = expect(restore ? local.restore(REVIEW) : local.sources()).rejects.toMatchObject(
      { unknownOutcome: true },
    );
    await vi.advanceTimersByTimeAsync(restore ? 120_001 : 30_001);
    await pending;
    expect(fetch.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
  },
);

it("recognizes only the restore-specific canonical review-required response", async () => {
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValue(json({ code: "local_privacy_review_required", review_id: REVIEW }, 409));
  await expect(client(fetch).restore(REVIEW)).rejects.toBeInstanceOf(OcrReviewRequired);
  expect(fetch).toHaveBeenCalledTimes(1);
  const read = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValue(json({ code: "local_privacy_review_required", review_id: REVIEW }, 409));
  await expect(client(read).sources()).rejects.not.toBeInstanceOf(OcrReviewRequired);
});

it("requires the exact bound result in an explicit restart response", async () => {
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValueOnce(json({ state: "restarted", result_id: REVIEW }))
    .mockResolvedValueOnce(json({ state: "restarted", result_id: ITEM }));
  const review = client(fetch).ocrReviews;
  await review.restart(REVIEW, REVIEW);
  expect(fetch.mock.calls[0]?.[0]).toBe(
    `http://127.0.0.1:9000/local-privacy/restore-reviews/${REVIEW}/restart`,
  );
  expect(fetch.mock.calls[0]?.[1]?.body).toBe("{}");
  await expect(review.restart(REVIEW, REVIEW)).rejects.toThrow();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it.each([
  [502, { code: "local_privacy_review_required", review_id: REVIEW }],
  [409, { code: "local_privacy_review_required", review_id: "not-an-id" }],
  [409, { code: "other", review_id: REVIEW }],
  [409, { code: "local_privacy_review_required", review_id: REVIEW, extra: "private" }],
])("rejects noncanonical restoration review response %s", async (status, body) => {
  await expect(
    client(vi.fn().mockResolvedValue(json(body, status))).restore(REVIEW),
  ).rejects.not.toBeInstanceOf(OcrReviewRequired);
});

it.each([
  (view: OcrReviewView) => {
    view.review_id = ITEM;
  },
  (view: OcrReviewView) => {
    view.observations.push(view.observations[0]!);
  },
  (view: OcrReviewView) => {
    view.observations[0]!.bbox = [0, 0, 201, 10];
  },
  (view: OcrReviewView) => {
    view.items[0]!.observation_ids = [REVIEW];
  },
  (view: OcrReviewView) => {
    view.observations[0]!.review_item_id = null;
  },
])("rejects malformed or disconnected measurement evidence", async (mutate) => {
  const view = ocrView();
  mutate(view);
  await expect(
    client(vi.fn().mockResolvedValue(json(view))).ocrReviews.get(REVIEW),
  ).rejects.toThrow();
});

it("retains every observation and a missing original confidence", async () => {
  const view = ocrView();
  view.observations[0]!.confidence = null;
  expect(await client(vi.fn().mockResolvedValue(json(view))).ocrReviews.get(REVIEW)).toEqual(view);
});

async function imageFixture() {
  const bytes = new TextEncoder().encode("synthetic PNG transport bytes");
  const view = ocrView();
  view.pages[0]!.image_sha256 = Buffer.from(
    await webcrypto.subtle.digest("SHA-256", bytes),
  ).toString("hex");
  return { bytes, view };
}

it("requires an exact verified page before sending one explicitly bound transcription", async () => {
  const { bytes, view } = await imageFixture();
  const updated = structuredClone(view);
  updated.items[0]!.confirmed_reading = "visible synthetic text";
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValueOnce(new Response(bytes, { headers: { "Content-Type": "image/png" } }))
    .mockResolvedValueOnce(json(updated));
  const review = client(fetch).ocrReviews;
  expect(() => review.confirm(view, ITEM, "visible synthetic text")).toThrow();
  expect(fetch).not.toHaveBeenCalled();
  await review.page(view, 1);
  expect(() => review.confirm(view, PLACEHOLDER, "incorrect placeholder")).toThrow();
  expect(() => review.confirm(view, ITEM, " ")).toThrow();
  expect(await review.confirm(view, ITEM, "visible synthetic text")).toEqual(updated);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1]?.[0]).toBe(
    `http://127.0.0.1:9000/local-privacy/restore-reviews/${REVIEW}/items/${ITEM}/confirm`,
  );
  expect(fetch.mock.calls[1]?.[1]).toMatchObject({
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    headers: { Authorization: "Bearer synthetic-local-session" },
  });
  expect(JSON.parse(fetch.mock.calls[1]?.[1]?.body as string)).toEqual({
    review_digest: HASH,
    page_image_sha256: view.pages[0]!.image_sha256,
    reading: "visible synthetic text",
  });
});

it("does not authorize a page with changed bytes", async () => {
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValue(new Response("changed", { headers: { "Content-Type": "image/png" } }));
  const review = client(fetch).ocrReviews;
  await expect(review.page(ocrView(), 1)).rejects.toThrow();
  expect(() => review.confirm(ocrView(), ITEM, "visible text")).toThrow();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([
  (view: OcrReviewView) => {
    view.stage = "restored";
  },
  (view: OcrReviewView) => {
    view.review_digest = "b".repeat(64);
  },
  (view: OcrReviewView) => {
    view.observations[0]!.confidence = 0.99;
  },
  (view: OcrReviewView) => {
    view.observations.pop();
  },
  (view: OcrReviewView) => {
    view.items[1]!.confirmed_reading = "[PT-SYNTHETIC]";
  },
])("rejects confirmation that changes measurements, stage or another item", async (mutate) => {
  const { bytes, view } = await imageFixture();
  const updated = structuredClone(view);
  updated.items[0]!.confirmed_reading = "visible text";
  mutate(updated);
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValueOnce(new Response(bytes, { headers: { "Content-Type": "image/png" } }))
    .mockResolvedValueOnce(json(updated));
  const review = client(fetch).ocrReviews;
  await review.page(view, 1);
  await expect(review.confirm(view, ITEM, "visible text")).rejects.toThrow();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("requires the exact unexpired aggregate receipt for the current stage", () => {
  const view = completedView();
  expect(currentOcrReceipt(view)).toBeDefined();
  for (const mutate of [
    (v: OcrReviewView) => {
      v.stage = "restored";
    },
    (v: OcrReviewView) => {
      v.receipts[0]!.review_digest = "b".repeat(64);
    },
    (v: OcrReviewView) => {
      v.receipts[0]!.readings[0]!.confirmed_reading = "other";
    },
    (v: OcrReviewView) => {
      v.receipts[0]!.expires_at = "2020-01-01T00:00:00Z";
    },
    (v: OcrReviewView) => {
      v.items[0]!.confirmed_reading = null;
    },
  ]) {
    const changed = structuredClone(view);
    mutate(changed);
    expect(currentOcrReceipt(changed)).toBeUndefined();
  }
  const restored = completedView("restored");
  restored.receipts.unshift(...view.receipts);
  expect(currentOcrReceipt(restored)?.stage).toBe("restored");
});
