/** Canonical-shape approval contract inputs for unit regression only, not real case data. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ExportServiceError,
  type ApprovalDecisionCommand,
  type ExportsApi,
  type SubmitApprovalCommand,
} from "@/api/exports";
import { TransportError } from "@/api/problems";
import { ApprovalPanel, type BasisIssue, type ExportBasisState } from "@/features/ApprovalPanel";
import { ExportPanel } from "@/features/ExportPanel";
import { LanguageProvider } from "@/ui/Language";
import {
  RUN,
  SNAPSHOT_DIGEST,
  approval,
  basis,
  operation,
  readiness,
  readinessBlocker,
  serverBasis,
} from "./export-fixtures";

function api(overrides: Partial<ExportsApi> = {}): ExportsApi {
  return {
    readBasis: vi.fn(() => Promise.reject(new Error("readBasis not scripted"))),
    createExport: vi.fn(() => Promise.reject(new Error("createExport not scripted"))),
    readExport: vi.fn(() => Promise.reject(new Error("readExport not scripted"))),
    downloadArtifact: vi.fn(() => Promise.reject(new Error("downloadArtifact not scripted"))),
    submitBundle: vi.fn(() => Promise.reject(new Error("submitBundle not scripted"))),
    readBundle: vi.fn(() => Promise.reject(new Error("readBundle not scripted"))),
    downloadBundle: vi.fn(() => Promise.reject(new Error("downloadBundle not scripted"))),
    submitApproval: vi.fn(() => Promise.reject(new Error("submitApproval not scripted"))),
    readApproval: vi.fn(() => Promise.reject(new Error("readApproval not scripted"))),
    decideApproval: vi.fn(() => Promise.reject(new Error("decideApproval not scripted"))),
    ...overrides,
  };
}

function ready(value = serverBasis()): ExportBasisState {
  return { kind: "ready", basis: value };
}

function failed(issue: Partial<BasisIssue> & Pick<BasisIssue, "code">): ExportBasisState {
  return {
    kind: "error",
    issue: { status: null, serviceMessage: null, ...issue },
  };
}

function show(service: ExportsApi, state: ExportBasisState, onRefresh: () => void = vi.fn()) {
  return render(
    <LanguageProvider language="zh">
      <ApprovalPanel api={service} jobId="job-1" state={state} onRefresh={onRefresh} />
    </LanguageProvider>,
  );
}

const submitButton = () => screen.getByRole("button", { name: /送出核准申請/ });

describe("approval submission", () => {
  it("submits the server-pinned digests under a fresh key when ready", async () => {
    const sent: SubmitApprovalCommand[] = [];
    const service = api({
      submitApproval: vi.fn((_job: string, command: SubmitApprovalCommand) => {
        sent.push(command);
        return Promise.resolve(approval());
      }),
    });
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    const first = show(service, ready(), onRefresh);
    await user.click(submitButton());
    await screen.findByText("待核准");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      schema_version: "service-v1",
      run: RUN,
      calculation_snapshot_digest: SNAPSHOT_DIGEST,
      template_bundle: basis().templateBundle,
    });
    expect(sent[0]?.idempotency_key).toMatch(/^wb-/);
    expect(onRefresh).toHaveBeenCalledTimes(1);
    // A second, separate submission mints its own key: no key is ever reused on purpose.
    first.unmount();
    show(service, ready());
    await user.click(submitButton());
    await screen.findByText("待核准");
    expect(sent).toHaveLength(2);
    expect(sent[1]?.idempotency_key).toMatch(/^wb-/);
    expect(sent[1]?.idempotency_key).not.toBe(sent[0]?.idempotency_key);
  });

  it("renders deduplicated blockers and explains the disabled submit", () => {
    const state = ready(
      serverBasis({
        readiness: readiness({
          state: "pending_data",
          required_total: 5,
          required_satisfied: 3,
          blockers: [
            readinessBlocker(),
            readinessBlocker(),
            readinessBlocker({
              code: "missing_factor",
              message: "表5 缺少區域因素值",
              source_key: "table_5:factor:road_width",
              table: "table_5",
              needed: "road_width 的已確認值",
              action: "請補齊來源觀察值",
            }),
          ],
        }),
      }),
    );
    show(api(), state);
    expect(screen.getByText("資料待補")).toBeInTheDocument();
    expect(screen.getByText("資料待補").parentElement).toHaveTextContent("必要項 3 / 5");
    // Duplicate code+source_key collapses to one row; the distinct blocker stays.
    expect(screen.getAllByText("表4 R-01 尚未確認交易條件")).toHaveLength(1);
    expect(screen.getByText("表5 缺少區域因素值")).toBeInTheDocument();
    expect(screen.getByText(/road_width 的已確認值/)).toBeInTheDocument();
    // Raw keys stay behind a details fold.
    expect(screen.getByText("missing_factor")).toBeInTheDocument();
    expect(submitButton()).toBeDisabled();
    expect(screen.getByText(/目前無法送出：資料待補/)).toBeInTheDocument();
  });

  it("tolerates a replayed submit under the same key after an unknown outcome", async () => {
    const sent: SubmitApprovalCommand[] = [];
    const service = api({
      submitApproval: vi.fn((_job: string, command: SubmitApprovalCommand) => {
        sent.push(command);
        return sent.length === 1
          ? Promise.reject(new TransportError("The service did not answer in time."))
          : Promise.resolve(approval());
      }),
    });
    const user = userEvent.setup();
    show(service, ready());
    await user.click(submitButton());
    await screen.findByText(/無法確認核准申請是否已被受理/);
    await user.click(screen.getByRole("button", { name: "以同一請求重試" }));
    await screen.findByText("待核准");
    expect(sent).toHaveLength(2);
    expect(sent[1]).toEqual(sent[0]);
  });
});

describe("approval decisions", () => {
  it("approves through the decisions endpoint and rerenders the approved state", async () => {
    const sent: ApprovalDecisionCommand[] = [];
    const decided = approval({
      status: "approved",
      decision: {
        decision: "approve",
        actor: { actor_id: "director-1", kind: "human" },
        decided_at: 1_757_600_100,
        reason: null,
      },
    });
    const service = api({
      decideApproval: vi.fn(
        (_job: string, approvalId: string, command: ApprovalDecisionCommand) => {
          expect(approvalId).toBe("appr-1");
          sent.push(command);
          return Promise.resolve(decided);
        },
      ),
    });
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    show(service, ready(serverBasis({ approval: approval() })), onRefresh);
    expect(screen.getByText("待核准")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "核准" }));
    await user.click(screen.getByRole("button", { name: "確認送出決定" }));
    await screen.findByText("已核准");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ schema_version: "service-v1", decision: "approve" });
    expect(sent[0]?.idempotency_key).toMatch(/^wb-/);
    expect(sent[0]?.reason).toBeUndefined();
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("requires a reason before a return can be confirmed", async () => {
    const sent: ApprovalDecisionCommand[] = [];
    const service = api({
      decideApproval: vi.fn((_job: string, _id: string, command: ApprovalDecisionCommand) => {
        sent.push(command);
        return Promise.resolve(
          approval({
            status: "returned",
            decision: {
              decision: "return",
              actor: { actor_id: "director-1", kind: "human" },
              decided_at: 1_757_600_200,
              reason: command.reason ?? null,
            },
          }),
        );
      }),
    });
    const user = userEvent.setup();
    show(service, ready(serverBasis({ approval: approval() })));
    await user.click(screen.getByRole("button", { name: "退回" }));
    const confirm = screen.getByRole("button", { name: "確認送出決定" });
    expect(confirm).toBeDisabled();
    expect(screen.getByText(/退回與撤回皆須填寫理由/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("理由（必填）"), "表4 交易條件仍有疑義");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await screen.findByText("已退回");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ decision: "return", reason: "表4 交易條件仍有疑義" });
    expect(screen.getByText(/理由：表4 交易條件仍有疑義/)).toBeInTheDocument();
  });

  it("names the missing approval permission on a decision 403", async () => {
    const service = api({
      decideApproval: vi.fn(() => Promise.reject(new ExportServiceError("unauthorized", 403))),
    });
    const user = userEvent.setup();
    show(service, ready(serverBasis({ approval: approval() })));
    await user.click(screen.getByRole("button", { name: "核准" }));
    await user.click(screen.getByRole("button", { name: "確認送出決定" }));
    await screen.findByText(/您沒有核准權限，請由具核准權限的人員執行此決定/);
    // The raw permission name stays available, but behind the diagnostics fold.
    expect(screen.getByText("publish_artifact")).toBeInTheDocument();
    expect(screen.getByText("publish_artifact")).not.toBeVisible();
    expect(screen.getByText("待核准")).toBeInTheDocument();
  });
});

/** An approval the service has already approved, with its recorded approve decision. */
function approvedApproval() {
  return approval({
    status: "approved",
    decision: {
      decision: "approve",
      actor: { actor_id: "director-1", kind: "human" },
      decided_at: 1_757_600_100,
      reason: null,
    },
  });
}

describe("decision availability by status (P1-C)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers 核准 and 退回 while awaiting approval, and no 撤回", () => {
    show(api(), ready(serverBasis({ approval: approval() })));
    expect(screen.getByRole("button", { name: "核准" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "退回" })).toBeEnabled();
    // The service only accepts withdraw on an approved request; offering it here 409s.
    expect(screen.queryByRole("button", { name: "撤回" })).not.toBeInTheDocument();
  });

  it("offers only 撤回 once approved", () => {
    show(api(), ready(serverBasis({ approval: approvedApproval() })));
    expect(screen.getByText("已核准")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "撤回" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "核准" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退回" })).not.toBeInTheDocument();
  });

  it("offers no decision action once returned, withdrawn or superseded", () => {
    const terminal = [
      approval({
        status: "returned",
        decision: {
          decision: "return",
          actor: { actor_id: "director-1", kind: "human" },
          decided_at: 1_757_600_200,
          reason: "表4 交易條件仍有疑義",
        },
      }),
      approval({
        status: "withdrawn",
        decision: {
          decision: "withdraw",
          actor: { actor_id: "reviewer-1", kind: "human" },
          decided_at: 1_757_600_300,
          reason: "送錯版本",
        },
      }),
      approval({ status: "superseded", decision: null }),
    ];
    const pills = ["已退回", "已撤回", "已失效（版本已變更）"];
    terminal.forEach((record, index) => {
      const view = show(api(), ready(serverBasis({ approval: record })));
      expect(screen.getByText(pills[index] as string)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "核准" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "退回" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "撤回" })).not.toBeInTheDocument();
      view.unmount();
    });
  });

  it("withdraws an approved request with a required reason and shows the receipt", async () => {
    const sent: ApprovalDecisionCommand[] = [];
    const withdrawn = approval({
      status: "withdrawn",
      decision: {
        decision: "withdraw",
        actor: { actor_id: "director-1", kind: "human" },
        decided_at: 1_757_600_400,
        reason: "誤核准，需重新審查",
      },
    });
    const service = api({
      decideApproval: vi.fn(
        (_job: string, approvalId: string, decisionCommand: ApprovalDecisionCommand) => {
          expect(approvalId).toBe("appr-1");
          sent.push(decisionCommand);
          return Promise.resolve(withdrawn);
        },
      ),
    });
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    show(service, ready(serverBasis({ approval: approvedApproval() })), onRefresh);
    await user.click(screen.getByRole("button", { name: "撤回" }));
    const confirm = screen.getByRole("button", { name: "確認送出決定" });
    expect(confirm).toBeDisabled();
    expect(screen.getByText(/退回與撤回皆須填寫理由/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("理由（必填）"), "誤核准，需重新審查");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await screen.findByText("已撤回");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      schema_version: "service-v1",
      decision: "withdraw",
      reason: "誤核准，需重新審查",
    });
    expect(sent[0]?.idempotency_key).toMatch(/^wb-/);
    expect(screen.getByText(/理由：誤核准，需重新審查/)).toBeInTheDocument();
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("retries an unknown withdraw outcome with the same key and payload", async () => {
    const calls: ApprovalDecisionCommand[] = [];
    const withdrawn = approval({
      status: "withdrawn",
      decision: {
        decision: "withdraw",
        actor: { actor_id: "director-1", kind: "human" },
        decided_at: 1_757_600_500,
        reason: "誤核准，需重新審查",
      },
    });
    const service = api({
      decideApproval: vi.fn(
        (_job: string, _id: string, decisionCommand: ApprovalDecisionCommand) => {
          calls.push(decisionCommand);
          return calls.length === 1
            ? Promise.reject(new TransportError("The service did not answer in time."))
            : Promise.resolve(withdrawn);
        },
      ),
    });
    const user = userEvent.setup();
    show(service, ready(serverBasis({ approval: approvedApproval() })));
    await user.click(screen.getByRole("button", { name: "撤回" }));
    await user.type(screen.getByLabelText("理由（必填）"), "誤核准，需重新審查");
    await user.click(screen.getByRole("button", { name: "確認送出決定" }));
    await screen.findByText(/無法確認決定是否已被記錄/);
    // Confirming again replays the SAME decision, approval id, key and payload.
    await user.click(screen.getByRole("button", { name: "確認送出決定" }));
    await screen.findByText("已撤回");
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual(calls[0]);
    expect(calls[0]?.idempotency_key).toMatch(/^wb-/);
  });

  it("explains a failed key mint on a decision and sends nothing", async () => {
    vi.stubGlobal("crypto", {});
    const decideApproval = vi.fn(() => Promise.reject(new Error("decideApproval not scripted")));
    const service = api({ decideApproval });
    const user = userEvent.setup();
    show(service, ready(serverBasis({ approval: approval() })));
    await user.click(screen.getByRole("button", { name: "核准" }));
    await user.click(screen.getByRole("button", { name: "確認送出決定" }));
    await screen.findByText(/無法產生操作識別碼，請更新瀏覽器或改用安全連線/);
    expect(decideApproval).not.toHaveBeenCalled();
    // The panel is not stuck: the confirm step stays usable for a later attempt.
    expect(screen.getByRole("button", { name: "確認送出決定" })).toBeEnabled();
  });
});

describe("basis problems and the formal option", () => {
  it("shows a withdrawn request, and the export panel explains the formal refusal", () => {
    const withdrawn = approval({
      status: "withdrawn",
      decision: {
        decision: "withdraw",
        actor: { actor_id: "reviewer-1", kind: "human" },
        decided_at: 1_757_600_300,
        reason: "送錯版本",
      },
    });
    const panel = show(api(), ready(serverBasis({ approval: withdrawn })));
    expect(screen.getByText("已撤回")).toBeInTheDocument();
    expect(screen.getByText(/理由：送錯版本/)).toBeInTheDocument();
    panel.unmount();
    render(
      <LanguageProvider language="zh">
        <ExportPanel api={api()} jobId="job-1" basis={basis()} approval={withdrawn} />
      </LanguageProvider>,
    );
    expect(screen.getByRole("radio", { name: /正式/ })).toBeEnabled();
    expect(screen.getByText(/核准申請已撤回，需重新送核，正式匯出將被拒絕/)).toBeInTheDocument();
  });

  it("renders four different basis problems instead of hiding the panel", () => {
    const cases: Array<[BasisIssue["code"], number, RegExp]> = [
      ["unauthorized", 403, /請重新登入或確認權限/],
      ["not_found", 404, /案件不存在/],
      ["version_conflict", 409, /資料或版本尚未就緒/],
      ["capability_unavailable", 503, /匯出服務尚未組裝/],
    ];
    const texts = cases.map(([code, status, pattern]) => {
      const view = show(api(), failed({ code, status }));
      // The panel stays mounted and names the problem; it is never silently hidden.
      expect(screen.getByRole("region", { name: "報表與核准" })).toBeInTheDocument();
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent(pattern);
      const text = alert.textContent ?? "";
      view.unmount();
      return text;
    });
    expect(new Set(texts).size).toBe(4);
  });

  it("surfaces the real meaning of a formal 409 and marks a formal operation", async () => {
    const conflicting = api({
      createExport: vi.fn(() =>
        Promise.reject(new ExportServiceError("version_conflict", 409, null)),
      ),
    });
    const user = userEvent.setup();
    const refused = render(
      <LanguageProvider language="zh">
        <ExportPanel api={conflicting} jobId="job-1" basis={basis()} approval={null} />
      </LanguageProvider>,
    );
    await user.click(screen.getByRole("radio", { name: /正式/ }));
    await user.click(screen.getByRole("button", { name: /產生並下載/ }));
    await screen.findByText(/正式匯出遭拒：尚未核准/);
    refused.unmount();

    const formalOperation = operation({
      status: "succeeded",
      requested_mode: "formal",
      effective_mode: "formal",
      tables: [{ table: "table_3", delivered: true, artifact_id: "a3", problem: null }],
      artifacts: [
        {
          artifact_id: "a3",
          kind: "official_workbook",
          content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          filename: "表3.xlsx",
          size_bytes: 1024,
          content_hash: "d".repeat(64),
        },
      ],
    });
    const succeeding = api({ createExport: vi.fn(() => Promise.resolve(formalOperation)) });
    render(
      <LanguageProvider language="zh">
        <ExportPanel
          api={succeeding}
          jobId="job-1"
          basis={basis()}
          approval={approval({ status: "approved" })}
        />
      </LanguageProvider>,
    );
    await user.click(screen.getByRole("radio", { name: /正式/ }));
    await user.click(screen.getByRole("button", { name: /產生並下載/ }));
    await screen.findByText("已完成");
    expect(screen.getAllByText("正式（已核准）")).toHaveLength(2);
    expect(screen.queryByText("草稿（未正式核定）")).not.toBeInTheDocument();
  });
});
