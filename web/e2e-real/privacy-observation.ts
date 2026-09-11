import { writeFileSync } from "node:fs";
import type { Page, Request, Response } from "@playwright/test";
import { responseDiagnostic, type PrivacyDiagnostic } from "../src/privacy/diagnostics";

type Endpoint =
  | "restore"
  | "final_download"
  | "review_view"
  | "review_page"
  | "review_confirm"
  | "review_restart";
type Phase =
  | "request_started"
  | "response_headers"
  | "request_finished"
  | "request_failed"
  | "observation_ended";
export interface PrivacyHttpObservation extends PrivacyDiagnostic {
  phase: Phase;
  endpoint: Endpoint;
  method: "GET" | "POST";
  elapsed_ms: number;
}
function endpoint(path: string): Endpoint | null {
  if (/^\/local-privacy\/restore\/[^/]+$/.test(path)) return "restore";
  if (/^\/local-privacy\/restored\/[^/]+$/.test(path)) return "final_download";
  if (/^\/local-privacy\/restore-reviews\/[^/]+$/.test(path)) return "review_view";
  if (/^\/local-privacy\/restore-reviews\/[^/]+\/pages\/\d+$/.test(path)) return "review_page";
  if (/^\/local-privacy\/restore-reviews\/[^/]+\/items\/[^/]+\/confirm$/.test(path))
    return "review_confirm";
  if (/^\/local-privacy\/restore-reviews\/[^/]+\/restart$/.test(path)) return "review_restart";
  return null;
}

export function observePrivacyHttp(
  page: Page,
  bridgeUrl: string,
  save: (record: PrivacyHttpObservation) => void,
  now = () => performance.now(),
) {
  const origin = new URL(bridgeUrl).origin;
  const pending = new Map<
    Request,
    { start: number; endpoint: Endpoint; method: "GET" | "POST"; diagnostic: PrivacyDiagnostic }
  >();
  let count = 0;
  let overflow = false;
  function record(
    phase: Phase,
    state: {
      start: number;
      endpoint: Endpoint;
      method: "GET" | "POST";
      diagnostic: PrivacyDiagnostic;
    },
  ) {
    if (count++ >= 512) {
      overflow = true;
      return;
    }
    save({
      phase,
      endpoint: state.endpoint,
      method: state.method,
      elapsed_ms: Math.max(0, Math.round((now() - state.start) * 1000) / 1000),
      ...state.diagnostic,
    });
  }
  function started(request: Request) {
    const url = new URL(request.url());
    const route = endpoint(url.pathname);
    const method = request.method();
    if (url.origin !== origin || !route || (method !== "GET" && method !== "POST")) return;
    if (pending.size >= 128) {
      overflow = true;
      return;
    }
    const state: {
      start: number;
      endpoint: Endpoint;
      method: "GET" | "POST";
      diagnostic: PrivacyDiagnostic;
    } = {
      start: now(),
      endpoint: route,
      method,
      diagnostic: responseDiagnostic(undefined, undefined),
    };
    pending.set(request, state);
    record("request_started", state);
  }
  function received(response: Response) {
    const state = pending.get(response.request());
    if (!state) return;
    state.diagnostic = responseDiagnostic(response.status(), new Headers(response.headers()));
    record("response_headers", state);
  }
  function ended(phase: "request_finished" | "request_failed", request: Request) {
    const state = pending.get(request);
    if (!state) return;
    record(phase, state);
    pending.delete(request);
  }
  const finished = (request: Request) => ended("request_finished", request);
  const failed = (request: Request) => ended("request_failed", request);
  page.on("request", started);
  page.on("response", received);
  page.on("requestfinished", finished);
  page.on("requestfailed", failed);
  return () => {
    page.off("request", started);
    page.off("response", received);
    page.off("requestfinished", finished);
    page.off("requestfailed", failed);
    for (const state of pending.values()) record("observation_ended", state);
    pending.clear();
    if (overflow) throw new Error("The bounded local HTTP observation limit was exceeded.");
  };
}

export function recordPrivacyHttp(page: Page, bridgeUrl: string, output: string) {
  writeFileSync(output, "", { flag: "wx", mode: 0o600 });
  return observePrivacyHttp(page, bridgeUrl, (record) =>
    writeFileSync(output, JSON.stringify(record) + "\n", { flag: "a", mode: 0o600 }),
  );
}
