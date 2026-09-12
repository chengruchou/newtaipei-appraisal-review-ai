/**
 * Operation-key minting that also works on insecure origins.
 *
 * The deployed demo is served over plain HTTP on a public IP, which is not a secure
 * context, so `crypto.randomUUID` does not exist there. Every place that mints an
 * idempotency or operation key goes through this helper: it prefers `randomUUID`,
 * falls back to an RFC 4122 version-4 UUID built from `crypto.getRandomValues`, and
 * when neither source of cryptographic randomness exists it throws a typed error.
 * It never silently returns a weak or predictable key.
 */

/** Thrown when the runtime offers no cryptographic randomness at all. */
export class OperationKeySourceError extends Error {
  constructor() {
    super(
      "No cryptographic randomness is available: neither crypto.randomUUID nor crypto.getRandomValues exists.",
    );
    this.name = "OperationKeySourceError";
  }
}

/** The slice of the Web Crypto surface this module needs; either member may be absent. */
export interface RandomSource {
  randomUUID?: () => string;
  getRandomValues?: (array: Uint8Array) => Uint8Array;
}

/**
 * Mint a fresh operation key: `<prefix>-<uuid v4>`.
 *
 * One key per user intent. A retry of an unknown outcome must reuse the key it
 * already holds, never call this again.
 */
export function newOperationKey(
  prefix: string,
  source: RandomSource | undefined = globalThis.crypto,
): string {
  return `${prefix}-${uuidV4(source)}`;
}

function uuidV4(source: RandomSource | undefined): string {
  if (typeof source?.randomUUID === "function") {
    return source.randomUUID();
  }
  if (typeof source?.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    source.getRandomValues(bytes);
    bytes[6] = ((bytes[6] as number) & 0x0f) | 0x40; // version 4
    bytes[8] = ((bytes[8] as number) & 0x3f) | 0x80; // variant 10xx
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return [
      hex.slice(0, 8),
      hex.slice(8, 12),
      hex.slice(12, 16),
      hex.slice(16, 20),
      hex.slice(20),
    ].join("-");
  }
  throw new OperationKeySourceError();
}
