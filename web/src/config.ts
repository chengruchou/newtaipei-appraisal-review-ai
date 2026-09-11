import { ReviewClient } from "./api/client";

/**
 * The API origin is a build-time setting, never a value the page discovers at runtime.
 * It is not a secret, but it is also not something a reviewer should be able to point at
 * an arbitrary host from the address bar.
 */
const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "";

const TOKEN_KEY = "workbench.session-token";
const PAIR_TOKEN_KEY = "workbench.original-pairing";
export const ORIGINAL_PREVIEW_URL: string = import.meta.env.VITE_LOCAL_ORIGINAL_PREVIEW_URL ?? "";

export function readPairToken(): string | null {
  try {
    return sessionStorage.getItem(PAIR_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function writePairToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(PAIR_TOKEN_KEY, token);
    else sessionStorage.removeItem(PAIR_TOKEN_KEY);
  } catch {
    /* Pairing remains optional and tab scoped. */
  }
}

/**
 * Identity comes from the deployment. The workbench holds a token only for the lifetime of
 * the tab and never decodes it: #25 forbids the frontend inferring its own permissions, so
 * what a reviewer may do is whatever the service answers, not what a claim says.
 *
 * sessionStorage rather than localStorage, so closing the tab ends the session.
 */
export function readToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function writeToken(token: string | null): void {
  try {
    if (token === null) {
      sessionStorage.removeItem(TOKEN_KEY);
      sessionStorage.removeItem(PAIR_TOKEN_KEY);
    } else {
      sessionStorage.setItem(TOKEN_KEY, token);
    }
  } catch {
    // A browser with site data blocked simply has no session; that is not an error worth
    // interrupting the reviewer over.
  }
}

export function buildClient(
  token: string | null = readToken(),
  onUnauthorized?: () => void,
  pairingToken: string | null = readPairToken(),
): ReviewClient {
  // Sync today, but the seam is a promise so a deployment can refresh a token here.
  return new ReviewClient({
    baseUrl: BASE_URL,
    token: () => Promise.resolve(token),
    ...(onUnauthorized ? { onUnauthorized } : {}),
    ...(ORIGINAL_PREVIEW_URL && pairingToken
      ? {
          localOriginalPreview: { baseUrl: ORIGINAL_PREVIEW_URL, pairingToken },
        }
      : {}),
  });
}
