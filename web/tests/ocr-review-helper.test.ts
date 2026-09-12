import { randomUUID } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import type { APIRequestContext, Page } from "@playwright/test";
import {
  localPrivacyGet,
  localPrivacyPost,
  selectReadyOcrItem,
} from "../e2e-real/ocr-review-helper";
import { rehearsalGet } from "../e2e-real/privacy-http";

it("preserves the original core request options and deadline without retry", async () => {
  const response = { ok: () => true };
  const operation = vi.fn().mockResolvedValue(response);
  const request = { get: operation } as unknown as APIRequestContext;
  const options = { headers: { Origin: "http://127.0.0.1:4174" } };
  expect(await rehearsalGet(request, "/v1/review-jobs/current", options)).toBe(response);
  expect(operation).toHaveBeenCalledExactlyOnceWith("/v1/review-jobs/current", options);
  expect(operation.mock.calls[0]?.[1]).not.toHaveProperty("timeout");
});

it("drops raw core transport errors without altering the failure outcome", async () => {
  const detail = "Private synthetic request details";
  const operation = vi.fn().mockRejectedValue(new Error(detail));
  const request = { get: operation } as unknown as APIRequestContext;
  const error = await rehearsalGet(request, "/v1/review-jobs/current", {}).catch(
    (caught: unknown) => caught,
  );
  expect(error).toBeInstanceOf(Error);
  expect((error as Error).message).toBe(
    "The rehearsal API request failed. Inspect the private server diagnostic.",
  );
  expect((error as Error).cause).toBeUndefined();
  expect((error as Error).stack).not.toContain(detail);
  expect(operation).toHaveBeenCalledTimes(1);
});

it.each(["get", "post"] as const)(
  "bounds one dedicated local %s request without retry",
  async (method) => {
    const response = { ok: () => true };
    const operation = vi.fn().mockResolvedValue(response);
    const request = { [method]: operation } as unknown as APIRequestContext;
    const headers = { Authorization: `Bearer ${randomUUID()}` };
    const call = method === "get" ? localPrivacyGet : localPrivacyPost;
    expect(await call(request, "http://127.0.0.1:8788/local-privacy/review", headers)).toBe(
      response,
    );
    expect(operation).toHaveBeenCalledExactlyOnceWith(
      "http://127.0.0.1:8788/local-privacy/review",
      {
        headers,
        timeout: 35_000,
        ...(method === "post" ? { data: {} } : {}),
      },
    );
  },
);

it.each(["get", "post"] as const)(
  "sanitizes local %s transport errors without retaining credentials or cause",
  async (method) => {
    const detail = "Synthetic transport request detail that must remain private";
    const operation = vi.fn().mockRejectedValue(new Error(detail));
    const request = { [method]: operation } as unknown as APIRequestContext;
    const call = method === "get" ? localPrivacyGet : localPrivacyPost;
    let caught: unknown;
    try {
      await call(request, "http://127.0.0.1:8788/local-privacy/review", {
        Authorization: `Bearer ${randomUUID()}`,
      });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(Error);
    const error = caught as Error;
    expect(error.message).toBe(
      "The bounded local privacy request failed. Inspect the private server diagnostic.",
    );
    expect(error.cause).toBeUndefined();
    expect(error.stack).not.toContain(detail);
    expect(error.stack).not.toContain("Bearer");
    expect(operation).toHaveBeenCalledTimes(1);
  },
);

afterEach(() => vi.useRealTimers());

function imageReadinessFixture(mode: "delayed" | "absent" | "refused") {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] });
  const image = document.createElement("img");
  Object.defineProperties(image, {
    complete: { value: true },
    naturalWidth: { value: mode === "refused" ? 0 : 640 },
  });
  const presentAt = mode === "absent" ? Infinity : mode === "refused" ? 0 : 20_000;
  const evaluateAll = vi.fn((read: (images: HTMLImageElement[]) => boolean) =>
    Promise.resolve(read(performance.now() >= presentAt ? [image] : [])),
  );
  const evaluate = vi.fn((read: (image: HTMLImageElement) => boolean) => {
    if (performance.now() >= presentAt) return Promise.resolve(read(image));
    return new Promise<boolean>((resolve, reject) => {
      setTimeout(
        () => {
          if (performance.now() >= presentAt) resolve(read(image));
          else reject(new Error("Synthetic locator action deadline exceeded."));
        },
        Math.min(15_000, presentAt - performance.now()),
      );
    });
  });
  const pageSelection = vi.fn().mockResolvedValue(undefined);
  const itemSelection = vi.fn().mockResolvedValue(undefined);
  const confirm = vi.fn();
  const page = {
    getByLabel: (label: string) => {
      if (label === "OCR review page") return { selectOption: pageSelection };
      if (label === "Required OCR item") return { selectOption: itemSelection };
      throw new Error("Unexpected selection in the readiness helper.");
    },
    getByRole: (role: string, options: { name: string; exact: boolean }) => {
      expect(role).toBe("img");
      expect(options).toEqual({ name: "OCR review full page 2", exact: true });
      return { evaluate, evaluateAll };
    },
  } as unknown as Page;
  const item = { page: 2, item_id: randomUUID() };
  let settled = false;
  let failure: unknown;
  const completion = selectReadyOcrItem(page, item)
    .then(() => {
      confirm();
    })
    .catch((error: unknown) => {
      failure = error;
    })
    .finally(() => {
      settled = true;
    });
  return {
    pageSelection,
    itemSelection,
    confirm,
    evaluate,
    evaluateAll,
    item,
    completion,
    settled: () => settled,
    failure: () => failure,
  };
}

it("waits for an image arriving after 15 seconds within the existing 30-second budget", async () => {
  const f = imageReadinessFixture("delayed");
  await vi.advanceTimersByTimeAsync(19_000);
  expect(f.settled()).toBe(false);
  expect(f.itemSelection).not.toHaveBeenCalled();
  expect(f.confirm).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(2_000);
  await f.completion;
  expect(f.failure()).toBeUndefined();
  expect(f.pageSelection).toHaveBeenCalledExactlyOnceWith("2");
  expect(f.itemSelection).toHaveBeenCalledExactlyOnceWith(f.item.item_id);
  expect(f.confirm).toHaveBeenCalledTimes(1);
  expect(f.evaluate).not.toHaveBeenCalled();
  expect(f.evaluateAll).toHaveBeenCalled();
});

it.each(["absent", "refused"] as const)(
  "keeps %s images blocking item selection and confirmation through the existing budget",
  async (mode) => {
    const f = imageReadinessFixture(mode);
    await vi.advanceTimersByTimeAsync(29_000);
    expect(f.settled()).toBe(false);
    expect(f.itemSelection).not.toHaveBeenCalled();
    expect(f.confirm).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1_001);
    await f.completion;
    expect(f.failure()).toBeInstanceOf(Error);
    expect((f.failure() as Error).message).toContain("30000ms");
    expect(f.itemSelection).not.toHaveBeenCalled();
    expect(f.confirm).not.toHaveBeenCalled();
    expect(f.pageSelection).toHaveBeenCalledExactlyOnceWith("2");
    expect(f.evaluate).not.toHaveBeenCalled();
  },
);
