import { randomUUID } from "node:crypto";
import { expect, it, vi } from "vitest";
import { BridgeError, LocalPrivacyClient } from "@/privacy/client";
import { responseDiagnostic } from "@/privacy/diagnostics";
import { OcrReviewRequired } from "@/privacy/ocr-review-client";
import { SOURCE } from "./privacy-fixtures";

function headers() {
  return new Headers({
    "X-Privacy-Request-Id": randomUUID(),
    "X-Privacy-Failure-Stage": "mapping",
    "X-Privacy-Failure-Code": "mapping_expired",
  });
}
it("retains actual status and the server's bounded diagnostic reference", () => {
  const h = headers();
  expect(responseDiagnostic(409, h)).toEqual({
    httpStatus: 409,
    requestId: h.get("X-Privacy-Request-Id"),
    serverFailure: { stage: "mapping", code: "mapping_expired" },
    transportFailure: null,
  });
});
it.each(["X-Privacy-Failure-Stage", "X-Privacy-Failure-Code", "X-Privacy-Request-Id"])(
  "discards malformed %s without echoing its value",
  (name) => {
    const h = headers();
    const value = "private synthetic diagnostic canary";
    h.set(name, value);
    const diagnostic = responseDiagnostic(409, h);
    expect(diagnostic.httpStatus).toBe(409);
    expect(diagnostic.serverFailure).toBeNull();
    expect(JSON.stringify(diagnostic)).not.toContain(value);
  },
);
it.each(["X-Privacy-Failure-Stage", "X-Privacy-Failure-Code", "X-Privacy-Request-Id"])(
  "uses a generic fallback for missing %s",
  (name) => {
    const h = headers();
    h.delete(name);
    expect(responseDiagnostic(409, h).serverFailure).toBeNull();
  },
);
it("does not turn success headers into a server rejection", () => {
  expect(responseDiagnostic(200, headers()).serverFailure).toBeNull();
  expect(responseDiagnostic(undefined, undefined)).toEqual({
    httpStatus: null,
    requestId: null,
    serverFailure: null,
    transportFailure: null,
  });
});
it("keeps generic error bodies private while preserving the actual rejection", async () => {
  const canary = "private synthetic server body";
  const h = headers();
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValue(
      new Response(JSON.stringify({ error: canary }), { status: 409, headers: h }),
    );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:8788",
    token: () => randomUUID(),
    fetch,
  });
  let error: unknown;
  try {
    await client.restore(SOURCE);
  } catch (caught) {
    error = caught;
  }
  expect(error).toBeInstanceOf(BridgeError);
  expect(error).toMatchObject({
    unknownOutcome: false,
    diagnostic: {
      httpStatus: 409,
      requestId: h.get("X-Privacy-Request-Id"),
      serverFailure: { stage: "mapping", code: "mapping_expired" },
    },
  });
  expect(String(error)).not.toContain(canary);
  expect(JSON.stringify(error)).not.toContain(canary);
  expect((error as Error).cause).toBeUndefined();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("still opens canonical OCR review when the 409 carries diagnostic headers", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
    new Response(JSON.stringify({ code: "local_privacy_review_required", review_id: SOURCE }), {
      status: 409,
      headers: headers(),
    }),
  );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:8788",
    token: () => randomUUID(),
    fetch,
  });
  await expect(client.restore(SOURCE)).rejects.toBeInstanceOf(OcrReviewRequired);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("does not invent a status or server cause for a transport failure", async () => {
  const canary = "private synthetic transport error";
  const fetch = vi.fn<typeof globalThis.fetch>().mockRejectedValue(new Error(canary));
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:8788",
    token: () => randomUUID(),
    fetch,
  });
  let error: unknown;
  try {
    await client.restore(SOURCE);
  } catch (caught) {
    error = caught;
  }
  expect(error).toMatchObject({
    unknownOutcome: true,
    diagnostic: {
      httpStatus: null,
      requestId: null,
      serverFailure: null,
      transportFailure: "request_failed",
    },
  });
  expect(JSON.stringify(error)).not.toContain(canary);
  expect((error as Error).cause).toBeUndefined();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("distinguishes response headers from a body that exceeds the existing client deadline", async () => {
  vi.useFakeTimers();
  try {
    const response = new Response("", { headers: { "X-Privacy-Request-Id": randomUUID() } });
    vi.spyOn(response, "json").mockReturnValue(new Promise(() => {}));
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(response);
    const client = new LocalPrivacyClient({
      baseUrl: "http://127.0.0.1:8788",
      token: () => randomUUID(),
      fetch,
      timeoutMs: 20,
    });
    const result = client.sources();
    const rejected = expect(result).rejects.toMatchObject({
      unknownOutcome: true,
      diagnostic: {
        httpStatus: 200,
        serverFailure: null,
        transportFailure: "deadline_exceeded",
      },
    });
    await vi.advanceTimersByTimeAsync(20);
    await rejected;
    expect(fetch).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});
