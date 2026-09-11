import type { APIRequestContext, APIResponse } from "@playwright/test";

export async function rehearsalGet(
  request: APIRequestContext,
  url: string,
  options: Parameters<APIRequestContext["get"]>[1],
): Promise<APIResponse> {
  try {
    return await request.get(url, options);
  } catch {
    throw new Error("The rehearsal API request failed. Inspect the private server diagnostic.");
  }
}

async function localPrivacyRequest(
  request: APIRequestContext,
  method: "GET" | "POST",
  url: string,
  headers: Record<string, string>,
): Promise<APIResponse> {
  try {
    return method === "GET"
      ? await request.get(url, { headers, timeout: 35_000 })
      : await request.post(url, { headers, data: {}, timeout: 35_000 });
  } catch {
    throw new Error(
      "The bounded local privacy request failed. Inspect the private server diagnostic.",
    );
  }
}
export function localPrivacyGet(
  request: APIRequestContext,
  url: string,
  headers: Record<string, string>,
) {
  return localPrivacyRequest(request, "GET", url, headers);
}
export function localPrivacyPost(
  request: APIRequestContext,
  url: string,
  headers: Record<string, string>,
) {
  return localPrivacyRequest(request, "POST", url, headers);
}
