import { webcrypto } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { ReviewClient, type ArtifactManifest } from "@/api/client";
import { TransportError } from "@/api/problems";
import { citation } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

it("binds source requests to opaque identity, version and exact bytes", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const bytes = new TextEncoder().encode("synthetic PDF transport bytes");
  const digest = await webcrypto.subtle.digest("SHA-256", bytes);
  const hash = Buffer.from(digest).toString("hex");
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(new Response(bytes, { headers: { "Content-Type": "application/pdf" } })),
  );
  const token = vi.fn(() => Promise.resolve("development-session"));
  const client = new ReviewClient({ baseUrl: "http://service", token, fetch, timeoutMs: 1000 });
  const received = await client.readSource({
    ...citation,
    document_id: "forms-id",
    version: "v2",
    content_hash: hash,
  });
  expect(Array.from(new Uint8Array(received))).toEqual(Array.from(bytes));
  expect(fetch.mock.calls[0]?.[0]).toBe(
    `http://service/v1/documents/forms-id/content?version=v2&content_hash=${hash}`,
  );
  expect(token).toHaveBeenCalledTimes(1);
  const artifact = { artifact_id: "result-id", content_hash: hash } as ArtifactManifest;
  await client.downloadArtifact("job-id", artifact);
  expect(token).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1]?.[0]).toBe(
    "http://service/v1/review-jobs/job-id/artifacts/result-id/content",
  );
});

it("rejects replaced PDF bytes before they can be rendered or saved", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const client = new ReviewClient({
    baseUrl: "http://service",
    token: () => Promise.resolve(null),
    fetch: () =>
      Promise.resolve(new Response("replaced", { headers: { "Content-Type": "application/pdf" } })),
    timeoutMs: 1000,
  });
  await expect(client.readSource(citation)).rejects.toBeInstanceOf(TransportError);
});

it("does not treat an authorization refusal as downloadable bytes", async () => {
  const client = new ReviewClient({
    baseUrl: "http://service",
    token: () => Promise.resolve(null),
    fetch: () => Promise.resolve(new Response('{"code":"not_found"}', { status: 404 })),
  });
  await expect(client.readSource(citation)).rejects.toMatchObject({ code: "not_found" });
});
