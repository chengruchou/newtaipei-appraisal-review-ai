/** Wire-level unit regression for the candidate client; no live service is involved. */
import { describe, expect, it, vi } from "vitest";
import { TransportError } from "@/api/problems";
import {
  CandidateClient,
  CandidateServiceError,
  type ConfirmCandidateCommand,
} from "@/features/candidate-api";

const CASE = "44444444-4444-4444-4444-444444444444";
const CAND = "55555555-5555-5555-5555-555555555555";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function candidatePayload() {
  return {
    candidate_id: CAND,
    case_id: CASE,
    revision_id: "rev-9",
    subject_id: "subject-1",
    field_key: "table_5.P002.market_distance",
    value: "850",
    unit: "m",
    applicable_date: "2026-07-01",
    source_id: "ntpc-open-data",
    evidence: {
      url: "https://data.ntpc.gov.tw/datasets/123",
      sha256: "e".repeat(64),
      retrieved_at: 1_757_500_000,
      excerpt: "",
      row_locator: null,
    },
    status: "candidate",
    created_by: { actor_id: "fetcher-1", kind: "system" },
    created_at: 1_757_500_100,
  };
}

function confirmCommand(): ConfirmCandidateCommand {
  return {
    schema_version: "service-v1",
    idempotency_key: "wb-unit-key",
    decision: "accept",
    expected_revision: "rev-9",
    accepted_value: "850",
    accepted_unit: "m",
    accepted_applicable_date: "2026-07-01",
    evidence_sha256: "e".repeat(64),
  };
}

describe("CandidateClient", () => {
  it("sends the bearer token per request and parses the list", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResponse(200, { case_id: CASE, candidates: [candidatePayload()] })),
    );
    const client = new CandidateClient({
      baseUrl: "http://unit-api",
      token: () => Promise.resolve("token-1"),
      fetch: fetchMock,
    });
    const list = await client.listCandidates(CASE);
    expect(list.candidates).toHaveLength(1);
    expect(list.candidates[0]?.field_key).toBe("table_5.P002.market_distance");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`http://unit-api/v1/cases/${CASE}/candidates`);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer token-1");
    expect(init.credentials).toBe("omit");
  });

  it("posts the confirm body verbatim to the confirm route", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse(201, {
          receipt_id: "rcpt-1",
          candidate_id: CAND,
          case_id: CASE,
          subject_id: "subject-1",
          field_key: "table_5.P002.market_distance",
          expected_revision: "rev-9",
          accepted_value: "850",
          accepted_unit: "m",
          accepted_applicable_date: "2026-07-01",
          evidence_sha256: "e".repeat(64),
          decision: "accept",
          reason: null,
          actor: { actor_id: "reviewer-1", kind: "human" },
          decided_at: 1_757_600_000,
          idempotency_key: "wb-unit-key",
        }),
      ),
    );
    const client = new CandidateClient({
      baseUrl: "",
      token: () => Promise.resolve("token-1"),
      fetch: fetchMock,
    });
    const receipt = await client.confirmCandidate(CASE, CAND, confirmCommand());
    expect(receipt.decision).toBe("accept");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/v1/cases/${CASE}/candidates/${CAND}/confirm`);
    expect(JSON.parse(init.body as string)).toEqual(confirmCommand());
  });

  it("treats only the canonical problem envelope as a refusal", async () => {
    const refusing = new CandidateClient({
      baseUrl: "",
      token: () => Promise.resolve(null),
      fetch: () =>
        Promise.resolve(
          jsonResponse(403, {
            schema_version: "service-v1",
            code: "unauthorized",
            message: "Service operation could not be completed.",
          }),
        ),
    });
    await expect(refusing.listCandidates(CASE)).rejects.toMatchObject({
      name: "CandidateServiceError",
      code: "unauthorized",
      status: 403,
    });

    const gateway = new CandidateClient({
      baseUrl: "",
      token: () => Promise.resolve(null),
      fetch: () => Promise.resolve(new Response("<html>bad gateway</html>", { status: 502 })),
    });
    // A gateway's HTML is an unknown outcome, never a decision the service made.
    await expect(gateway.listCandidates(CASE)).rejects.toBeInstanceOf(TransportError);
    await expect(gateway.listCandidates(CASE)).rejects.not.toBeInstanceOf(CandidateServiceError);
  });
});
