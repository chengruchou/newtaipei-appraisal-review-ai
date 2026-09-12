import {
  requestLoginCode,
  verifyLoginCode,
  type AuthPlaneOptions,
  type LoginGrant,
} from "@/api/client";

/**
 * Glue between the entry screens and the login/intake plane, plus the small display
 * rules the case list uses. Kept out of the components so tests can pin them down.
 *
 * The API origin is the same build-time setting the main client uses (src/config.ts
 * reads the identical variable); it is repeated here rather than imported so this
 * module owns no configuration file.
 */
const AUTH_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "";

export interface AuthApi {
  requestCode(email: string): Promise<void>;
  verify(email: string, code: string): Promise<LoginGrant>;
}

export function buildAuthApi(options?: Partial<AuthPlaneOptions>): AuthApi {
  const plane: AuthPlaneOptions = { baseUrl: AUTH_BASE_URL, ...options };
  return {
    requestCode: (email) => requestLoginCode(plane, email),
    verify: (email, code) => verifyLoginCode(plane, email, code),
  };
}

/**
 * Not a deliverability check — only a guard against obviously incomplete input, so a
 * typo fails locally instead of consuming a rate-limited request. The service stays
 * the authority on what it accepts.
 */
export function looksLikeEmail(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed);
}

/**
 * 顯示編號: a short, stable handle derived from the service's case id, for reading a
 * list aloud. Deterministic (last 8 base36-ish characters, uppercased) and never a
 * substitute for the full id, which stays available verbatim next to it.
 */
export function shortDisplayId(caseId: string): string {
  const compact = caseId.replace(/[^0-9A-Za-z]/g, "");
  if (compact === "") return caseId;
  return compact.slice(-8).toUpperCase();
}

/**
 * Renders a service timestamp (ISO string, epoch seconds or epoch milliseconds) in
 * zh-TW wall-clock form. An unreadable value returns null so callers say "尚未提供"
 * instead of showing "Invalid Date".
 */
export function formatExpiry(value: string | number | null): string | null {
  if (value === null) return null;
  let date: Date;
  if (typeof value === "number") {
    // Epoch seconds vs milliseconds: anything before ~2001-09 in ms is treated as seconds.
    date = new Date(value < 1_000_000_000_000 ? value * 1000 : value);
  } else {
    date = new Date(value);
  }
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** Keeps long emails / filenames readable in a fixed row; the full text goes in title=. */
export function truncateMiddle(text: string, max = 34): string {
  if (text.length <= max) return text;
  const head = Math.ceil((max - 1) / 2);
  const tail = Math.floor((max - 1) / 2);
  return `${text.slice(0, head)}…${text.slice(text.length - tail)}`;
}

/** Blob.arrayBuffer with a FileReader fallback for older engines. */
export function readFileBytes(file: Blob): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("The file could not be read."));
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.readAsArrayBuffer(file);
  });
}

/** Bytes for humans; the exact count stays in title= where callers need it. */
export function formatBytes(size: number | null): string | null {
  if (size === null || !Number.isFinite(size) || size < 0) return null;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
