/**
 * The client's job is to turn a service answer into something the UI can act on without
 * guessing. The cases worth pinning are the ones where guessing is tempting: a timeout,
 * where the outcome is genuinely unknown, and an error body that is missing or malformed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewClient } from "@/api/client";
import { ServiceError, TransportError } from "@/api/problems";
import { view } from "./fixtures";

function clientWith(fetchImpl: typeof globalThis.fetch, timeoutMs = 50): ReviewClient {
  return new ReviewClient({
    baseUrl: "http://service",
    token: () => Promise.resolve("token-1"),
    fetch: fetchImpl,
    timeoutMs,
  });
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ReviewClient", () => {
  // Auth/shape assertions must not race cold schema compilation or parallel worker load.
  // Advance the fake clock explicitly in the deadline test below.
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends the bearer token but never puts identity in the body", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(200, view()));
    await clientWith(fetchImpl).readTask("t1");

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer token-1");
    expect(init.body).toBeUndefined();
  });

  it("maps a service problem body onto its machine code", async () => {
    // A fresh Response per call: a body can only be read once, so a shared instance would
    // make the second assertion measure the mock rather than the client.
    const fetchImpl = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(jsonResponse(409, { code: "version_conflict", message: "…" })),
      );
    const client = clientWith(fetchImpl);

    await expect(client.readTask("t1")).rejects.toThrow(ServiceError);
    await expect(client.readTask("t1")).rejects.toMatchObject({
      code: "version_conflict",
      status: 409,
    });
  });

  it("falls back to the status when the error body is unusable", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(new Response("<html>gateway</html>", { status: 503 }));

    await expect(
      clientWith(fetchImpl as unknown as typeof fetch).readTask("t1"),
    ).rejects.toMatchObject({ code: "capability_unavailable" });
  });

  it("reports a timeout as a transport failure, not as a service decision", async () => {
    const fetchImpl = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            const error = new Error("aborted");
            error.name = "AbortError";
            reject(error);
          });
        }),
    );

    const pending = clientWith(fetchImpl as unknown as typeof fetch, 10)
      .readTask("t1")
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(11);
    const failure = await pending;

    // A ServiceError here would tell the UI the service decided something. It did not.
    expect(failure).toBeInstanceOf(TransportError);
    expect(failure).not.toBeInstanceOf(ServiceError);
    expect((failure as TransportError).retryable).toBe(true);
  });

  it("percent-encodes a path segment rather than pasting it into the URL", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(200, view()));
    await clientWith(fetchImpl).readTask("../v1/review-jobs");

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      "http://service/v1/review-tasks/..%2Fv1%2Freview-jobs",
    );
  });
});
