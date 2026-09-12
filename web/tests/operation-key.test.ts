/**
 * The deployed demo runs on plain HTTP, where `crypto.randomUUID` does not exist.
 * Key minting must still produce real RFC 4122 v4 UUIDs from `getRandomValues`,
 * and must refuse loudly - never weakly - when no randomness exists at all.
 */
import { webcrypto } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";

import { newIdempotencyKey } from "@/api/client";
import { newOperationKey, OperationKeySourceError, type RandomSource } from "@/api/ids";

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** A crypto with getRandomValues but no randomUUID, as an insecure origin provides. */
function insecureOriginCrypto(): RandomSource {
  return { getRandomValues: (array: Uint8Array) => webcrypto.getRandomValues(array) };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("newOperationKey without crypto.randomUUID", () => {
  it("mints valid, unique v4 UUIDs from getRandomValues alone", () => {
    const source = insecureOriginCrypto();
    const seen = new Set<string>();
    for (let draw = 0; draw < 100; draw += 1) {
      const key = newOperationKey("wb", source);
      expect(key.startsWith("wb-")).toBe(true);
      const uuid = key.slice("wb-".length);
      expect(uuid).toMatch(V4);
      // The regex already pins these; assert them explicitly as the contract.
      expect(uuid[14]).toBe("4"); // version nibble
      expect(["8", "9", "a", "b"]).toContain(uuid[19]); // variant 10xx
      seen.add(key);
    }
    expect(seen.size).toBe(100);
  });

  it("forces the version and variant bits regardless of the raw bytes", () => {
    for (const fill of [0x00, 0xff]) {
      const source: RandomSource = {
        getRandomValues: (array: Uint8Array) => {
          array.fill(fill);
          return array;
        },
      };
      const uuid = newOperationKey("x", source).slice(2);
      expect(uuid).toMatch(V4);
      expect(uuid[14]).toBe("4");
      expect(uuid[19]).toBe(fill === 0 ? "8" : "b");
    }
  });

  it("prefers randomUUID when the runtime provides it", () => {
    const source: RandomSource = {
      randomUUID: () => "11111111-2222-4333-8444-555555555555",
      getRandomValues: () => {
        throw new Error("must not be reached when randomUUID exists");
      },
    };
    expect(newOperationKey("wb", source)).toBe("wb-11111111-2222-4333-8444-555555555555");
  });

  it("throws a typed error when neither randomness source exists", () => {
    expect(() => newOperationKey("wb", {})).toThrow(OperationKeySourceError);
    // A runtime with no crypto global at all.
    vi.stubGlobal("crypto", undefined);
    expect(() => newOperationKey("wb")).toThrow(OperationKeySourceError);
  });
});

describe("newIdempotencyKey on an insecure origin", () => {
  it("falls back to getRandomValues when the global crypto lacks randomUUID", () => {
    vi.stubGlobal("crypto", insecureOriginCrypto());
    const key = newIdempotencyKey();
    expect(key.slice("wb-".length)).toMatch(V4);
  });

  it("throws the typed error when the global crypto offers nothing", () => {
    vi.stubGlobal("crypto", {});
    expect(() => newIdempotencyKey()).toThrow(OperationKeySourceError);
  });

  it("still honors an injected random source", () => {
    vi.stubGlobal("crypto", {});
    expect(newIdempotencyKey(() => "pinned")).toBe("wb-pinned");
  });
});
