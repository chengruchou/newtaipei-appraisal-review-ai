import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import { expect, it, vi } from "vitest";
import type { Page, Request, Response } from "@playwright/test";
import { observePrivacyHttp, type PrivacyHttpObservation } from "../e2e-real/privacy-observation";

function fixture() {
  const page = new EventEmitter();
  const canary = randomUUID();
  const spies = { headers: vi.fn(), postData: vi.fn(), failure: vi.fn() };
  const request = {
    url: () => `http://127.0.0.1:8788/local-privacy/restored/${canary}`,
    method: () => "GET",
    ...spies,
  } as unknown as Request;
  const response = {
    request: () => request,
    status: () => 200,
    headers: () => ({ "x-privacy-request-id": randomUUID(), "private-canary": canary }),
  } as unknown as Response;
  const records: PrivacyHttpObservation[] = [];
  let clock = 0;
  const finish = observePrivacyHttp(
    page as unknown as Page,
    "http://127.0.0.1:8788",
    (record) => records.push(record),
    () => clock,
  );
  return {
    page,
    request,
    response,
    canary,
    spies,
    records,
    finish,
    time: (value: number) => {
      clock = value;
    },
  };
}
it("separately measures response headers and completed body without inspecting request values", () => {
  const f = fixture();
  f.page.emit("request", f.request);
  f.time(12);
  f.page.emit("response", f.response);
  f.time(14.318);
  f.page.emit("requestfinished", f.request);
  f.finish();
  expect(f.records.map((r) => [r.phase, r.elapsed_ms, r.httpStatus])).toEqual([
    ["request_started", 0, null],
    ["response_headers", 12, 200],
    ["request_finished", 14.318, 200],
  ]);
  expect(f.records.every((record) => record.endpoint === "final_download")).toBe(true);
  expect(JSON.stringify(f.records)).not.toContain(f.canary);
  expect(f.spies.headers).not.toHaveBeenCalled();
  expect(f.spies.postData).not.toHaveBeenCalled();
});
it("retains an unfinished observation without inventing a server rejection", () => {
  const f = fixture();
  f.page.emit("request", f.request);
  f.time(15_000);
  f.finish();
  expect(f.records.at(-1)).toMatchObject({
    phase: "observation_ended",
    httpStatus: null,
    serverFailure: null,
    elapsed_ms: 15_000,
  });
});
it("does not retain raw transport failure messages", () => {
  const f = fixture();
  f.page.emit("request", f.request);
  f.page.emit("requestfailed", f.request);
  f.finish();
  expect(f.records.at(-1)).toMatchObject({ phase: "request_failed", httpStatus: null });
  expect(f.spies.failure).not.toHaveBeenCalled();
});
it("fails explicitly when the bounded observation capacity is exceeded", () => {
  const f = fixture();
  for (let index = 0; index < 200; index++) {
    f.page.emit("request", f.request);
    f.page.emit("response", f.response);
    f.page.emit("requestfinished", f.request);
  }
  expect(() => f.finish()).toThrow("bounded local HTTP observation limit");
  expect(f.records).toHaveLength(512);
});
