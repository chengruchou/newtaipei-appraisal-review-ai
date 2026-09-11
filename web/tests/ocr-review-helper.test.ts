import { randomUUID } from "node:crypto";
import { expect, it, vi } from "vitest";
import type { APIRequestContext } from "@playwright/test";
import { localPrivacyGet, localPrivacyPost } from "../e2e-real/ocr-review-helper";
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
