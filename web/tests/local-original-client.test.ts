/** Fake-fetch unit regressions only. No original document or real service is accessed. */
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ReviewClient,
  type ClientOptions,
  type ReviewSessionView,
  type SourceCitation,
} from "@/api/client";
import { ServiceError, TransportError, type ServiceErrorCode } from "@/api/problems";
import { citation } from "./fixtures";

const API = "https://unit-review.invalid";
const PREVIEW = "http://127.0.0.1:18766";
const PAIR = "unit-pair-credential";
const REVIEW = "unit-review-session";
const bytes = new TextEncoder().encode("Unit PDF transport bytes, not a real document");
const session = (mode: ReviewSessionView["data_mode"]): ReviewSessionView => ({
  schema_version: "service-v1",
  actor: { schema_version: "service-v1", actor_id: "unit-actor", kind: "human" },
  data_mode: mode,
  configured_jobs: [],
});
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const problem = (code: ServiceErrorCode, status: number) =>
  json(
    { schema_version: "service-v1", code, message: "Service operation could not be completed." },
    status,
  );
const pdf = () => new Response(bytes, { headers: { "Content-Type": "application/pdf" } });

function requestUrl(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

async function source(): Promise<SourceCitation> {
  return {
    ...citation,
    document_id: "unit document/原件?part=1#source",
    version: "unit version/2?",
    content_hash: Buffer.from(await webcrypto.subtle.digest("SHA-256", bytes)).toString("hex"),
  };
}

function options(fetch: typeof globalThis.fetch): ClientOptions {
  return {
    baseUrl: API,
    token: () => Promise.resolve(REVIEW),
    localOriginalPreview: { baseUrl: PREVIEW, pairingToken: PAIR },
    fetch,
    timeoutMs: 2000,
  };
}

function sessionThen(mode: ReviewSessionView["data_mode"], response: () => Promise<Response>) {
  return vi.fn<typeof globalThis.fetch>((input) =>
    requestUrl(input) === `${API}/v1/review-session`
      ? Promise.resolve(json(session(mode)))
      : response(),
  );
}

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("local original source transport (unit regression)", () => {
  it.each(["absent", "empty"])(
    "requires separate pairing when pairing is %s, without an API fallback",
    async (pairing) => {
      const fetch = sessionThen("local_original", () => Promise.resolve(pdf()));
      const config = options(fetch);
      if (pairing === "absent") delete config.localOriginalPreview;
      else config.localOriginalPreview!.pairingToken = "";
      const client = new ReviewClient(config);
      await client.readSession();
      const failure: unknown = await client
        .readSource(await source())
        .catch((error: unknown) => error);
      expect(failure).toBeInstanceOf(ServiceError);
      expect(failure).toMatchObject({ code: "capability_unavailable", status: 503 });
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it.each([
    "http://unit-preview.invalid:18766",
    "http://192.168.0.1:18766",
    "http://0.0.0.0:18766",
    "http://127.0.0.1.unit-preview.invalid:18766",
    "http://localhost:18766",
    "http://[::1]:18766",
    "https://127.0.0.1:18766",
    "http://127.0.0.1",
    `${PREVIEW}/private`,
    `${PREVIEW}/?pair=unit-value`,
    `${PREVIEW}/#unit-fragment`,
    "http://unit-user:unit-password@127.0.0.1:18766",
  ])("rejects an origin outside the configured numeric loopback policy: %s", (baseUrl) => {
    const fetch = vi.fn<typeof globalThis.fetch>();
    expect(
      () =>
        new ReviewClient({
          ...options(fetch),
          localOriginalPreview: { baseUrl, pairingToken: PAIR },
        }),
    ).toThrow("Original preview requires a configured numeric loopback origin");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("sends pairing and review authority separately, with exact identity and no URL credentials", async () => {
    const fetch = sessionThen("local_original", () => Promise.resolve(pdf()));
    const client = new ReviewClient(options(fetch));
    const ref = await source();
    await client.readSession();
    const received = await client.readSource(ref);
    expect(Array.from(new Uint8Array(received))).toEqual(Array.from(bytes));
    expect(fetch).toHaveBeenCalledTimes(2);
    const [input, init] = fetch.mock.calls[1]!;
    const url = new URL(requestUrl(input));
    expect(url.origin).toBe(PREVIEW);
    expect(url.pathname).toBe(`/local-original/documents/${encodeURIComponent(ref.document_id)}`);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      version: ref.version,
      content_hash: ref.content_hash,
    });
    expect(url.username).toBe("");
    expect(url.password).toBe("");
    expect(url.hash).toBe("");
    expect(url.href).not.toContain(PAIR);
    expect(url.href).not.toContain(REVIEW);
    expect(init).toMatchObject({ credentials: "omit", redirect: "error", cache: "no-store" });
    expect(init?.body).toBeUndefined();
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${PAIR}`);
    expect(headers.get("X-Review-Session")).toBe(`Bearer ${REVIEW}`);
    expect(headers.get("Accept")).toBe("application/pdf");
    const sessionHeaders = new Headers(fetch.mock.calls[0]![1]?.headers);
    expect(sessionHeaders.get("Authorization")).toBe(`Bearer ${REVIEW}`);
    expect(sessionHeaders.has("X-Review-Session")).toBe(false);
    expect([...sessionHeaders.values()].join(" ")).not.toContain(PAIR);
  });

  it("normalizes an accepted trailing slash instead of requesting a double-slash source path", async () => {
    const fetch = sessionThen("local_original", () => Promise.resolve(pdf()));
    const config = options(fetch);
    config.localOriginalPreview!.baseUrl = `${PREVIEW}/`;
    const client = new ReviewClient(config);
    const ref = await source();
    await client.readSession();
    await client.readSource(ref);
    expect(new URL(requestUrl(fetch.mock.calls[1]![0])).pathname).toBe(
      `/local-original/documents/${encodeURIComponent(ref.document_id)}`,
    );
  });

  it.each(["synthetic", "unspecified"] as const)(
    "does not send pairing credentials to the regular API in %s mode",
    async (mode) => {
      const fetch = sessionThen(mode, () => Promise.resolve(pdf()));
      const client = new ReviewClient(options(fetch));
      await client.readSession();
      await client.readSource(await source());
      const [input, init] = fetch.mock.calls[1]!;
      expect(requestUrl(input)).toContain(`${API}/v1/documents/`);
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBe(`Bearer ${REVIEW}`);
      expect(headers.has("X-Review-Session")).toBe(false);
      expect([...headers.values()].join(" ")).not.toContain(PAIR);
    },
  );

  it.each<[ServiceErrorCode, number]>([
    ["unauthorized", 403],
    ["not_found", 404],
    ["capability_unavailable", 503],
  ])("preserves typed paired-source %s and never falls back", async (code, status) => {
    const fetch = sessionThen("local_original", () => Promise.resolve(problem(code, status)));
    const onUnauthorized = vi.fn();
    const client = new ReviewClient({ ...options(fetch), onUnauthorized });
    await client.readSession();
    const failure: unknown = await client
      .readSource(await source())
      .catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ServiceError);
    expect(failure).toMatchObject({ code, status });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(requestUrl(fetch.mock.calls[1]![0])).toContain(`${PREVIEW}/local-original/`);
    expect(onUnauthorized).toHaveBeenCalledTimes(code === "unauthorized" ? 1 : 0);
  });

  it.each(["redirect", "redirect blocked", "wrong content type", "wrong bytes", "gateway"])(
    "rejects %s without following a redirect or trying the API",
    async (mode) => {
      const fetch = sessionThen("local_original", () => {
        if (mode === "redirect blocked")
          return Promise.reject(new TypeError("Unit fetch blocked redirect"));
        return Promise.resolve(
          mode === "redirect"
            ? new Response(null, {
                status: 302,
                headers: { Location: "https://unit-redirect.invalid/" },
              })
            : mode === "wrong content type"
              ? new Response(bytes, { headers: { "Content-Type": "text/html" } })
              : mode === "wrong bytes"
                ? new Response("replaced unit bytes", {
                    headers: { "Content-Type": "application/pdf" },
                  })
                : new Response("<html>Unit gateway failure</html>", { status: 503 }),
        );
      });
      const client = new ReviewClient(options(fetch));
      await client.readSession();
      await expect(client.readSource(await source())).rejects.toBeInstanceOf(TransportError);
      expect(fetch).toHaveBeenCalledTimes(2);
      expect(fetch.mock.calls[1]![1]?.redirect).toBe("error");
    },
  );

  it("aborts paired source requests on dispose and never returns a late response", async () => {
    let resolve: (response: Response) => void = () => {
      throw new Error("Unit request has not started");
    };
    const fetch = sessionThen(
      "local_original",
      () =>
        new Promise<Response>((complete) => {
          resolve = complete;
        }),
    );
    const client = new ReviewClient(options(fetch));
    await client.readSession();
    const ref = await source();
    const outcome = client.readSource(ref).catch((error: unknown) => error);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    const signal = fetch.mock.calls[1]![1]?.signal;
    client.dispose();
    expect(signal?.aborted).toBe(true);
    resolve(pdf());
    expect(await outcome).toBeInstanceOf(TransportError);
    await expect(client.readSource(ref)).rejects.toBeInstanceOf(TransportError);
    await expect(client.readSession()).rejects.toBeInstanceOf(TransportError);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
