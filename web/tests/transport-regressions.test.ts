import { expect, it, vi } from "vitest";
import { ReviewClient } from "@/api/client";
import { TransportError } from "@/api/problems";

function client(fetch: typeof globalThis.fetch) {
  return new ReviewClient({
    baseUrl: "http://service",
    token: () => Promise.resolve(null),
    fetch,
    timeoutMs: 20,
  });
}

it("keeps the deadline alive after headers while the body stalls", async () => {
  vi.useFakeTimers();
  try {
    let signal: AbortSignal | null | undefined;
    const fetch = vi.fn((_url: string | URL | Request, init?: RequestInit) => {
      signal = init?.signal;
      return Promise.resolve({ ok: true, text: () => new Promise(() => {}) } as Response);
    });
    let failure: unknown;
    void client(fetch)
      .readTask("t")
      .catch((error) => {
        failure = error;
      });
    await vi.advanceTimersByTimeAsync(21);
    expect(signal?.aborted).toBe(true);
    expect(failure).toBeInstanceOf(TransportError);
  } finally {
    vi.useRealTimers();
  }
});

it("classifies a body read interruption as an unknown transport outcome", async () => {
  const fetch = vi.fn(() =>
    Promise.resolve({
      ok: true,
      text: () => Promise.reject(new TypeError("body lost")),
    } as unknown as Response),
  );
  await expect(client(fetch).readTask("t")).rejects.toBeInstanceOf(TransportError);
});

it.each(["{", "null", "[]", "{}"])(
  "does not accept an invalid successful receipt: %s",
  async (body) => {
    const fetch = vi.fn(() => Promise.resolve(new Response(body)));
    await expect(client(fetch).submitResponse("t", {} as never)).rejects.toBeInstanceOf(
      TransportError,
    );
  },
);
