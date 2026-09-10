import { webcrypto } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { BridgeError, LocalPrivacyClient } from "@/privacy/client";
import { HASH, SOURCE, snapshot, preview } from "./privacy-fixtures";

afterEach(() => vi.unstubAllGlobals());

it.each([
  "https://example.com",
  "http://127.0.0.1:9000/?path=private",
  "http://user@localhost:9000",
  "http://localhost:9000/private",
])("refuses a noncanonical bridge base %s", (baseUrl) => {
  expect(() => new LocalPrivacyClient({ baseUrl, token: () => "development-session" })).toThrow();
});

it("uses only the configured loopback bridge and a separate session header", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(new Response(JSON.stringify({ sources: [{ source_id: SOURCE }] }))),
  );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:9000",
    token: () => "local-session",
    fetch,
  });
  expect(await client.sources()).toEqual([SOURCE]);
  expect(fetch.mock.calls[0]?.[0]).toBe("http://127.0.0.1:9000/local-privacy/sources");
  expect(fetch.mock.calls[0]?.[1]).toMatchObject({
    method: "GET",
    credentials: "omit",
    redirect: "error",
    headers: { Authorization: "Bearer local-session" },
  });
  expect(fetch.mock.calls[0]?.[1]?.body).toBeUndefined();
});

it("requires the authoritative digest header instead of inventing a local digest", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(new Response(JSON.stringify(snapshot().view))),
  );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:9000",
    token: () => "local-session",
    fetch,
  });
  await expect(client.reload()).rejects.toMatchObject({ unknownOutcome: true });
});

it("sends latest source revision and review digest verbatim with no asserted actor", async () => {
  const latest = snapshot();
  latest.digest = "b".repeat(64);
  latest.view.command.selection_revision = 3;
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(
      new Response(JSON.stringify(latest.view), {
        headers: { "X-Privacy-Review-Digest": latest.digest },
      }),
    ),
  );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:9000",
    token: () => "local-session",
    fetch,
  });
  await client.confirmReview(latest);
  const sent = fetch.mock.calls[0]?.[1]?.body;
  if (typeof sent !== "string") throw new Error("Expected a serialized JSON command");
  const body: unknown = JSON.parse(sent);
  expect(body).toEqual({
    schema_version: "local-privacy-v1",
    case_id: SOURCE,
    snapshot_id: SOURCE,
    revision: 3,
    review_digest: "b".repeat(64),
  });
});

it("never retries a transport-uncertain transfer", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.reject(new TypeError("connection lost")),
  );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:9000",
    token: () => "local-session",
    fetch,
  });
  await expect(client.transfer(SOURCE)).rejects.toBeInstanceOf(BridgeError);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0]?.[1]?.body).toBe("{}");
});

it("does not return an unexpected private field in a sanitized manifest", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          preview_id: SOURCE,
          reviewer_text: null,
          payload_digest: HASH,
          manifest: { raw_text: "synthetic-original" },
        }),
      ),
    ),
  );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:9000",
    token: () => "local-session",
    fetch,
  });
  await expect(client.prepare()).rejects.toBeInstanceOf(BridgeError);
  expect(fetch.mock.calls[0]?.[0]).toBe("http://127.0.0.1:9000/local-privacy/exports/preview");
});

it("verifies exact preview PDF bytes against the sanitized manifest", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const bytes = new TextEncoder().encode("synthetic preview transport bytes");
  const digest = Buffer.from(await webcrypto.subtle.digest("SHA-256", bytes)).toString("hex");
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(new Response(bytes, { headers: { "Content-Type": "application/pdf" } })),
  );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:9000",
    token: () => "local-session",
    fetch,
  });
  const exact = { ...preview, manifest: { ...preview.manifest, sanitized_digest: digest } };
  expect((await client.previewPdf(exact)).size).toBe(bytes.length);
  await expect(
    client.previewPdf({
      ...exact,
      manifest: { ...exact.manifest, sanitized_digest: "0".repeat(64) },
    }),
  ).rejects.toBeInstanceOf(BridgeError);
});

it("sends no reviewer text in the document-only C2 export preview", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(new Response(JSON.stringify(preview))),
  );
  const client = new LocalPrivacyClient({
    baseUrl: "http://127.0.0.1:9000",
    token: () => "local-session",
    fetch,
  });
  await client.prepare();
  expect(fetch.mock.calls[0]?.[1]?.body).toBe("{}");
});
