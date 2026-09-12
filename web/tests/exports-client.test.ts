import { webcrypto } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { ExportsClient, ExportServiceError } from "@/api/exports";
import { TransportError, UNKNOWN_OUTCOME } from "@/api/problems";
import { artifact, command, operation, XLSX_TYPE } from "./export-fixtures";

afterEach(() => vi.unstubAllGlobals());

function client(fetch: typeof globalThis.fetch): ExportsClient {
  return new ExportsClient({
    baseUrl: "http://service",
    token: () => Promise.resolve("development-session"),
    fetch,
    timeoutMs: 1000,
  });
}

function envelope(code: string, message = "Service operation could not be completed."): string {
  return JSON.stringify({ schema_version: "service-v1", code, message });
}

it("never returns an error envelope as downloadable bytes", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.resolve(
      new Response(envelope("unauthorized"), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  await expect(client(fetch).downloadArtifact("job-1", artifact())).rejects.toMatchObject({
    name: "ExportServiceError",
    code: "unauthorized",
    status: 403,
  });
  expect(fetch.mock.calls[0]?.[0]).toBe(
    "http://service/v1/review-jobs/job-1/artifacts/art-1/content",
  );
});

it("rejects a JSON body that arrives with HTTP 200 instead of file bytes", async () => {
  const fetch: typeof globalThis.fetch = () =>
    Promise.resolve(
      new Response('{"detail":"not a workbook"}', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  await expect(client(fetch).downloadArtifact("job-1", artifact())).rejects.toBeInstanceOf(
    TransportError,
  );
});

it("delivers verified bytes under the server's declared filename", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const bytes = new TextEncoder().encode("PK synthetic workbook bytes");
  const digest = await webcrypto.subtle.digest("SHA-256", bytes);
  const hash = Buffer.from(digest).toString("hex");
  const fetch: typeof globalThis.fetch = () =>
    Promise.resolve(
      new Response(bytes, {
        status: 200,
        headers: {
          "Content-Type": XLSX_TYPE,
          "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(
            "表3_地價區段勘查表.xlsx",
          )}`,
        },
      }),
    );
  const file = await client(fetch).downloadArtifact("job-1", artifact({ content_hash: hash }));
  expect(file.filename).toBe("表3_地價區段勘查表.xlsx");
  expect(file.contentType).toBe(XLSX_TYPE);
  expect(Array.from(new Uint8Array(file.bytes))).toEqual(Array.from(bytes));
});

it("rejects replaced bytes whose hash does not match the operation's manifest", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const fetch: typeof globalThis.fetch = () =>
    Promise.resolve(
      new Response("tampered", { status: 200, headers: { "Content-Type": XLSX_TYPE } }),
    );
  await expect(client(fetch).downloadArtifact("job-1", artifact())).rejects.toBeInstanceOf(
    TransportError,
  );
});

it("accepts a 202 operation and preserves the service's own 503 sentence", async () => {
  const accepted: typeof globalThis.fetch = () =>
    Promise.resolve(new Response(JSON.stringify(operation()), { status: 202 }));
  await expect(client(accepted).createExport("job-1", command())).resolves.toMatchObject({
    export_id: "exp-1",
    status: "queued",
  });
  const unavailable: typeof globalThis.fetch = () =>
    Promise.resolve(
      new Response(envelope("capability_unavailable", "Export routes are not deployed."), {
        status: 503,
      }),
    );
  const refusal = await client(unavailable)
    .createExport("job-1", command())
    .catch((cause: unknown) => cause);
  expect(refusal).toBeInstanceOf(ExportServiceError);
  expect(refusal).toMatchObject({
    code: "capability_unavailable",
    serviceMessage: "Export routes are not deployed.",
  });
});

it("treats a replayed key with different payload as the service's own conflict", async () => {
  const fetch: typeof globalThis.fetch = () =>
    Promise.resolve(new Response(envelope("version_conflict"), { status: 409 }));
  await expect(client(fetch).createExport("job-1", command())).rejects.toMatchObject({
    name: "ExportServiceError",
    code: "version_conflict",
    status: 409,
  });
});

it("keeps a gateway answer an unknown outcome rather than a refusal", async () => {
  const fetch: typeof globalThis.fetch = () =>
    Promise.resolve(new Response("<html>Bad gateway</html>", { status: 502 }));
  const outcome = await client(fetch)
    .createExport("job-1", command())
    .catch((cause: unknown) => cause);
  expect(outcome).toBeInstanceOf(TransportError);
  expect((outcome as TransportError).message).toBe(UNKNOWN_OUTCOME);
});

describe("sha256Hex fallback", () => {
  it("matches the RFC test vector without WebCrypto", async () => {
    const { sha256Hex } = await import("../src/api/exports");
    const subtle = Object.getOwnPropertyDescriptor(globalThis.crypto ?? {}, "subtle");
    // Simulate the insecure-origin deployment where crypto.subtle is undefined.
    if (globalThis.crypto)
      Object.defineProperty(globalThis.crypto, "subtle", { value: undefined, configurable: true });
    try {
      const abc = new TextEncoder().encode("abc");
      const buffer = abc.buffer.slice(abc.byteOffset, abc.byteOffset + abc.byteLength);
      expect(await sha256Hex(buffer)).toBe(
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
      );
      expect(await sha256Hex(new ArrayBuffer(0))).toBe(
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      );
    } finally {
      if (globalThis.crypto && subtle) Object.defineProperty(globalThis.crypto, "subtle", subtle);
    }
  });
});
