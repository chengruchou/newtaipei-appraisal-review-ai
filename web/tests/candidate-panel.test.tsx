/** Scripted-service unit regression for 資料核對; not real-case or backend acceptance. */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CandidatePanel } from "@/features/CandidatePanel";
import {
  CandidateServiceError,
  type CandidateApi,
  type CandidateConfirmation,
  type CandidateListView,
  type ConfirmCandidateCommand,
  type FactCandidate,
} from "@/features/candidate-api";
import { LanguageProvider } from "@/ui/Language";

const SHA = "e".repeat(64);

function candidate(overrides: Partial<FactCandidate> = {}): FactCandidate {
  return {
    candidate_id: "cand-1",
    case_id: "case-1",
    revision_id: "rev-9",
    subject_id: '["regional","板橋-A","三重-B"]:road_width:target',
    field_key: "table_5.P002.market_distance",
    value: "850",
    unit: "m",
    applicable_date: "2026-07-01",
    source_id: "ntpc-open-data",
    evidence: {
      url: "https://data.ntpc.gov.tw/datasets/123",
      sha256: SHA,
      retrieved_at: 1_757_500_000,
      excerpt: "市場距離 850 公尺",
      row_locator: "row-123",
    },
    status: "candidate",
    created_by: { actor_id: "fetcher-1", kind: "system" },
    created_at: 1_757_500_100,
    ...overrides,
  };
}

function listView(candidates: FactCandidate[]): CandidateListView {
  return { case_id: "case-1", candidates };
}

function receipt(overrides: Partial<CandidateConfirmation> = {}): CandidateConfirmation {
  return {
    receipt_id: "rcpt-1",
    candidate_id: "cand-1",
    case_id: "case-1",
    subject_id: candidate().subject_id,
    field_key: candidate().field_key,
    decision: "accept",
    reason: null,
    actor: { actor_id: "reviewer-1", kind: "human" },
    decided_at: 1_757_600_000,
    ...overrides,
  };
}

function api(overrides: Partial<CandidateApi> = {}): CandidateApi {
  return {
    listCandidates: vi.fn(() => Promise.reject(new Error("listCandidates not scripted"))),
    confirmCandidate: vi.fn(() => Promise.reject(new Error("confirmCandidate not scripted"))),
    ...overrides,
  };
}

function show(service: CandidateApi) {
  return render(
    <LanguageProvider language="zh">
      <CandidatePanel api={service} caseId="case-1" />
    </LanguageProvider>,
  );
}

describe("candidate list rendering", () => {
  it("says honestly when no external candidate exists", async () => {
    show(api({ listCandidates: vi.fn(() => Promise.resolve(listView([]))) }));
    await screen.findByText("尚無外部候選資料。");
  });

  it("shows a readable field label, value, source link and keeps digests behind 技術紀錄", async () => {
    show(api({ listCandidates: vi.fn(() => Promise.resolve(listView([candidate()]))) }));
    await screen.findByText("表5 · P002 · 市場距離");
    expect(screen.getByText("候選")).toBeVisible();
    expect(screen.getByText("850")).toBeVisible();
    expect(screen.getByText("2026-07-01")).toBeVisible();
    expect(screen.getByRole("link", { name: "查看來源頁面" })).toHaveAttribute(
      "href",
      "https://data.ntpc.gov.tw/datasets/123",
    );
    // Raw identifiers and the evidence digest stay available, but folded away.
    expect(screen.getByText(SHA)).toBeInTheDocument();
    expect(screen.getByText(SHA)).not.toBeVisible();
    expect(screen.getByText("table_5.P002.market_distance")).not.toBeVisible();
    expect(screen.getByText("rev-9")).not.toBeVisible();
  });

  it("renders an unavailable candidate capability honestly", async () => {
    show(
      api({
        listCandidates: vi.fn(() =>
          Promise.reject(new CandidateServiceError("capability_unavailable", 503)),
        ),
      }),
    );
    await screen.findByText(/候選資料功能尚未配置/);
    expect(screen.queryByRole("button", { name: "確認採用" })).not.toBeInTheDocument();
  });
});

describe("candidate decisions", () => {
  it("accepts by echoing the stored candidate exactly under a fresh wb- key", async () => {
    const sent: ConfirmCandidateCommand[] = [];
    const lists = [listView([candidate()]), listView([candidate({ status: "confirmed" })])];
    let reads = 0;
    const service = api({
      listCandidates: vi.fn(() => {
        const next = lists[Math.min(reads, lists.length - 1)] as CandidateListView;
        reads += 1;
        return Promise.resolve(next);
      }),
      confirmCandidate: vi.fn(
        (_case: string, candidateId: string, command: ConfirmCandidateCommand) => {
          expect(candidateId).toBe("cand-1");
          sent.push(command);
          return Promise.resolve(receipt());
        },
      ),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(await screen.findByRole("button", { name: "確認採用" }));
    // The confirm step repeats the exact value so nobody accepts blind.
    expect(screen.getByText(/即將確認採用此候選值/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "確認送出決定" }));
    await screen.findByText("已確認");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      schema_version: "service-v1",
      decision: "accept",
      expected_revision: "rev-9",
      accepted_value: "850",
      accepted_unit: "m",
      accepted_applicable_date: "2026-07-01",
      evidence_sha256: SHA,
    });
    expect(sent[0]?.idempotency_key).toMatch(/^wb-/);
    expect(sent[0]).not.toHaveProperty("reason");
  });

  it("requires a reason before a rejection can be confirmed", async () => {
    const sent: ConfirmCandidateCommand[] = [];
    const service = api({
      listCandidates: vi.fn(() => Promise.resolve(listView([candidate()]))),
      confirmCandidate: vi.fn((_case: string, _id: string, command: ConfirmCandidateCommand) => {
        sent.push(command);
        return Promise.resolve(receipt({ decision: "reject", reason: command.reason ?? null }));
      }),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(await screen.findByRole("button", { name: "拒絕" }));
    const confirm = screen.getByRole("button", { name: "確認送出決定" });
    expect(confirm).toBeDisabled();
    expect(screen.getByText("拒絕須填寫理由。")).toBeVisible();
    await user.type(screen.getByLabelText("理由（必填）"), "來源資料與現況不符");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ decision: "reject", reason: "來源資料與現況不符" });
  });

  it("hides decision buttons with a permission message after a 403", async () => {
    const service = api({
      listCandidates: vi.fn(() => Promise.resolve(listView([candidate()]))),
      confirmCandidate: vi.fn(() => Promise.reject(new CandidateServiceError("unauthorized", 403))),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(await screen.findByRole("button", { name: "確認採用" }));
    await user.click(screen.getByRole("button", { name: "確認送出決定" }));
    await screen.findByText(/需具確認權限的登入，此工作階段僅能檢視候選資料/);
    expect(screen.queryByRole("button", { name: "確認採用" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒絕" })).not.toBeInTheDocument();
    // The candidate itself is untouched: the pill still says 候選.
    expect(screen.getByText("候選")).toBeVisible();
  });

  it("explains a 409 as changed data and offers a refresh", async () => {
    const listCandidates = vi.fn(() => Promise.resolve(listView([candidate()])));
    const service = api({
      listCandidates,
      confirmCandidate: vi.fn(() =>
        Promise.reject(new CandidateServiceError("version_conflict", 409)),
      ),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(await screen.findByRole("button", { name: "確認採用" }));
    await user.click(screen.getByRole("button", { name: "確認送出決定" }));
    await screen.findByText(/資料已變更，請重新整理後再核對目前候選值/);
    await user.click(screen.getByRole("button", { name: "重新整理候選資料" }));
    await waitFor(() => expect(listCandidates).toHaveBeenCalledTimes(2));
  });
});
