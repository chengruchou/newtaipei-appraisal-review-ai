import { useEffect, useState } from "react";
import { newIdempotencyKey } from "@/api/client";
import {
  ExportServiceError,
  type ApprovalDecisionCommand,
  type ApprovalDecisionKind,
  type ExportReadiness,
  type ExportReadinessBlocker,
  type ExportsApi,
  type ReportApproval,
  type ServerExportBasis,
  type SubmitApprovalCommand,
} from "@/api/exports";
import type { ServiceErrorCode } from "@/api/problems";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";

/**
 * 報表與核准: the formal report approval flow. The panel shows the server-computed
 * readiness for the current content binding, submits the approval request from the
 * server-held basis (never an invented digest), and records decisions. History this
 * round is exactly the current approval and its decision; no separate history API exists.
 */

/** Why the basis read failed, kept distinguishable instead of collapsed to null. */
export interface BasisIssue {
  code: ServiceErrorCode | "transport";
  status: number | null;
  serviceMessage: string | null;
}

export type ExportBasisState =
  | { kind: "loading" }
  | { kind: "ready"; basis: ServerExportBasis }
  | { kind: "error"; issue: BasisIssue };

export function classifyBasisError(cause: unknown): BasisIssue {
  if (cause instanceof ExportServiceError)
    return { code: cause.code, status: cause.status, serviceMessage: cause.serviceMessage };
  return { code: "transport", status: null, serviceMessage: null };
}

export function basisIssueText(
  issue: BasisIssue,
  t: (english: string, chinese: string) => string,
): string {
  const words: Record<BasisIssue["code"], [string, string]> = {
    unauthorized: ["Sign in again or confirm your permissions.", "請重新登入或確認權限。"],
    not_found: ["This case does not exist for this session.", "案件不存在。"],
    version_conflict: ["The data or version is not ready yet.", "資料或版本尚未就緒。"],
    capability_unavailable: ["The export service is not assembled yet.", "匯出服務尚未組裝。"],
    invalid_request: [
      "The service rejected the basis request.",
      "服務未接受這次讀取，請重新整理後再試。",
    ],
    execution_failed: [
      "The service could not complete the basis read.",
      "服務未能完成讀取，請稍後再試。",
    ],
    transport: ["Could not connect to the service.", "無法連線。"],
  };
  return t(...words[issue.code]);
}

const APPROVAL_WORDS: Record<ReportApproval["status"], [string, string]> = {
  submitted: ["Awaiting approval", "待核准"],
  approved: ["Approved", "已核准"],
  returned: ["Returned", "已退回"],
  withdrawn: ["Withdrawn", "已撤回"],
  superseded: ["Superseded (content changed)", "已失效（版本已變更）"],
};

const READINESS_WORDS: Record<ExportReadiness["state"], [string, string]> = {
  pending_data: ["Data pending", "資料待補"],
  ready_to_submit: ["Ready to submit", "可送核"],
};

export function approvalStatusText(
  status: ReportApproval["status"],
  t: (english: string, chinese: string) => string,
): string {
  return t(...APPROVAL_WORDS[status]);
}

/**
 * Why a formal export would be refused right now, or null when a current approved
 * approval exists. Shared with the export panel's 正式 option.
 */
export function formalRefusalText(
  readiness: ExportReadiness | null | undefined,
  approval: ReportApproval | null | undefined,
  t: (english: string, chinese: string) => string,
): string | null {
  if (approval) {
    if (approval.status === "approved") return null;
    const why: Record<Exclude<ReportApproval["status"], "approved">, [string, string]> = {
      submitted: [
        "The approval request is still awaiting approval, so a formal export will be refused.",
        "核准申請仍為待核准，正式匯出將被拒絕。",
      ],
      returned: [
        "The approval request was returned; submit again after corrections.",
        "核准申請已退回，需修正後重新送核，正式匯出將被拒絕。",
      ],
      withdrawn: [
        "The approval request was withdrawn; submit a new request first.",
        "核准申請已撤回，需重新送核，正式匯出將被拒絕。",
      ],
      superseded: [
        "The approval no longer matches the current content (superseded); submit again.",
        "核准已失效（版本已變更），需重新送核，正式匯出將被拒絕。",
      ],
    };
    return t(...why[approval.status]);
  }
  if (readiness?.state === "pending_data")
    return t(
      "Required data is still pending, so no approval exists and a formal export will be refused.",
      "資料待補，尚無法送核，正式匯出將被拒絕。",
    );
  return t(
    "No approval request has been approved for the current content, so a formal export will be refused.",
    "目前內容尚未核准，正式匯出將被拒絕；請先於「報表與核准」完成送核與核准。",
  );
}

function formatServerSeconds(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    hour12: false,
  });
}

function dedupBlockers(blockers: ExportReadinessBlocker[]): ExportReadinessBlocker[] {
  const seen = new Set<string>();
  return blockers.filter((blocker) => {
    const key = `${blocker.code}::${blocker.source_key ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

interface SubmitAttempt {
  key: string;
  command: SubmitApprovalCommand;
}

/** An unknown-outcome decision; a retry replays exactly this approval, key and payload. */
interface DecisionAttempt {
  key: string;
  approvalId: string;
  command: ApprovalDecisionCommand;
}

type PanelNotice =
  | { kind: "service"; error: ExportServiceError; on: "submit" | "decision" }
  | { kind: "unknown-submit" }
  | { kind: "unknown-decision" }
  | { kind: "key-unavailable" };

function sameSubmitPayload(a: SubmitApprovalCommand, b: SubmitApprovalCommand): boolean {
  return (
    JSON.stringify({ ...a, idempotency_key: "" }) === JSON.stringify({ ...b, idempotency_key: "" })
  );
}

function sameDecisionPayload(a: ApprovalDecisionCommand, b: ApprovalDecisionCommand): boolean {
  return (
    JSON.stringify({ ...a, idempotency_key: "" }) === JSON.stringify({ ...b, idempotency_key: "" })
  );
}

/**
 * The decisions the service will accept for a given approval status. The backend
 * requires status "submitted" for approve/return and status "approved" for withdraw;
 * offering anything else is a guaranteed 409.
 */
export function availableDecisions(
  status: ReportApproval["status"],
): readonly ApprovalDecisionKind[] {
  if (status === "submitted") return ["approve", "return"];
  if (status === "approved") return ["withdraw"];
  return [];
}

export function ApprovalPanel({
  api,
  jobId,
  state,
  onRefresh,
}: {
  api: ExportsApi;
  jobId: string;
  state: ExportBasisState;
  onRefresh: () => void;
}) {
  const t = useText();
  const [busy, setBusy] = useState(false);
  /** An unknown submit outcome; a retry reuses exactly this key+payload. */
  const [pending, setPending] = useState<SubmitAttempt | null>(null);
  /** An unknown decision outcome; confirming again reuses exactly this key+payload. */
  const [pendingAttempt, setPendingAttempt] = useState<DecisionAttempt | null>(null);
  /** The freshest approval this panel has seen from an action response. */
  const [actionResult, setActionResult] = useState<ReportApproval | null>(null);
  const [notice, setNotice] = useState<PanelNotice | null>(null);
  const [pendingDecision, setPendingDecision] = useState<ApprovalDecisionKind | null>(null);
  const [reason, setReason] = useState("");

  const basis = state.kind === "ready" ? state.basis : null;
  useEffect(() => {
    // A fresh basis read is the durable truth; drop the local action echo.
    setActionResult(null);
  }, [basis]);

  const readiness = basis?.readiness ?? null;
  const approval = actionResult ?? basis?.approval ?? null;

  async function submit() {
    if (!basis || busy) return;
    const draft: SubmitApprovalCommand = {
      schema_version: "service-v1",
      idempotency_key: "",
      run: basis.run,
      calculation_snapshot_digest: basis.calculation_snapshot_digest,
      template_bundle: basis.template_bundle,
    };
    // An unknown outcome retries with the SAME key and payload, so a replay returns the
    // original approval. A fresh submit always mints a new key.
    const reuse = pending !== null && sameSubmitPayload(pending.command, draft);
    let key: string;
    try {
      // Minting can throw on runtimes with no cryptographic randomness at all; nothing
      // has been sent yet, so `pending` is untouched and no state is spent.
      key = reuse && pending ? pending.key : newIdempotencyKey();
    } catch {
      setNotice({ kind: "key-unavailable" });
      return;
    }
    const command: SubmitApprovalCommand = { ...draft, idempotency_key: key };
    setBusy(true);
    setNotice(null);
    try {
      const accepted = await api.submitApproval(jobId, command);
      setPending(null);
      setActionResult(accepted);
      onRefresh();
    } catch (cause) {
      if (cause instanceof ExportServiceError) {
        setPending(null);
        setNotice({ kind: "service", error: cause, on: "submit" });
      } else {
        setPending({ key, command });
        setNotice({ kind: "unknown-submit" });
      }
    } finally {
      setBusy(false);
    }
  }

  async function decide(kind: ApprovalDecisionKind) {
    if (!approval || busy) return;
    if (!availableDecisions(approval.status).includes(kind)) return;
    const trimmed = reason.trim();
    if ((kind === "return" || kind === "withdraw") && !trimmed) return;
    const draft: ApprovalDecisionCommand = {
      schema_version: "service-v1",
      idempotency_key: "",
      decision: kind,
      ...(kind === "approve" ? {} : { reason: trimmed }),
    };
    // An unknown outcome is retried with the SAME approval id, key and payload, so a
    // replay returns the recorded decision. Any other confirm mints a fresh key.
    const reuse =
      pendingAttempt !== null &&
      pendingAttempt.approvalId === approval.approval_id &&
      sameDecisionPayload(pendingAttempt.command, draft);
    let key: string;
    try {
      // Minting can throw on runtimes with no cryptographic randomness at all; nothing
      // has been sent yet, so no state is spent.
      key = reuse && pendingAttempt ? pendingAttempt.key : newIdempotencyKey();
    } catch {
      setNotice({ kind: "key-unavailable" });
      return;
    }
    const command: ApprovalDecisionCommand = { ...draft, idempotency_key: key };
    setBusy(true);
    setNotice(null);
    try {
      const next = await api.decideApproval(jobId, approval.approval_id, command);
      setPendingAttempt(null);
      setActionResult(next);
      setPendingDecision(null);
      setReason("");
      onRefresh();
    } catch (cause) {
      if (cause instanceof ExportServiceError) {
        // A definitive refusal: this exact request will not be accepted, so the key is spent.
        setPendingAttempt(null);
        setNotice({ kind: "service", error: cause, on: "decision" });
      } else {
        // The decision outcome is unknown; keep the exact attempt so confirming again
        // replays it, and re-read the durable state, which decides.
        setPendingAttempt({ key, approvalId: approval.approval_id, command });
        setNotice({ kind: "unknown-decision" });
        onRefresh();
      }
    } finally {
      setBusy(false);
    }
  }

  const blockedReason =
    state.kind !== "ready"
      ? t("The submission basis is not readable.", "尚未讀得送核依據。")
      : approval?.status === "submitted"
        ? t("An approval request is already awaiting approval.", "已有待核准的申請。")
        : approval?.status === "approved"
          ? t(
              "The current content is already approved; no new request is needed.",
              "目前內容已核准，無需重複送核。",
            )
          : !readiness
            ? t(
                "The service did not supply readiness for this content.",
                "服務未提供送核就緒資訊。",
              )
            : readiness.state === "pending_data"
              ? t("Required data is still pending.", "資料待補，必要項尚未齊備。")
              : null;
  const canSubmit = blockedReason === null;

  const decisionActions = approval ? availableDecisions(approval.status) : [];
  // A pending pick can go stale when a refresh lands a new status; never confirm a
  // decision the current status no longer accepts.
  const activeDecision =
    pendingDecision !== null && decisionActions.includes(pendingDecision) ? pendingDecision : null;
  const decisionWords: Record<ApprovalDecisionKind, [string, string]> = {
    approve: ["Approve", "核准"],
    return: ["Return", "退回"],
    withdraw: ["Withdraw", "撤回"],
  };

  return (
    <section className="panel" aria-label={t("Report and approval", "報表與核准")}>
      <h2>{t("Report and approval", "報表與核准")}</h2>
      <p className="small muted">
        {t(
          "A formal export requires an approved request for the current content. Readiness, requests and decisions below come from the service.",
          "正式匯出須先完成本內容版本的核准。以下就緒狀態、申請與決定皆來自服務紀錄。",
        )}
      </p>
      {state.kind === "loading" ? (
        <p role="status" className="muted">
          {t("Reading the submission basis…", "正在讀取送核依據…")}
        </p>
      ) : state.kind === "error" ? (
        <div className="notice" data-tone="warn" role="alert">
          <p style={{ margin: 0 }}>
            {t("The submission basis could not be read.", "送核依據目前無法讀取。")}{" "}
            {basisIssueText(state.issue, t)}
          </p>
          {state.issue.serviceMessage ? (
            <p className="small muted" style={{ margin: "0.25rem 0 0" }}>
              {t("Service message", "服務回覆")}：{state.issue.serviceMessage}
            </p>
          ) : null}
          <button type="button" disabled={busy} onClick={onRefresh}>
            {t("Read again", "重新讀取")}
          </button>
        </div>
      ) : (
        <>
          {readiness ? (
            <div aria-label={t("Submission readiness", "送核就緒狀態")}>
              <p role="status">
                <span className="status-pill" data-status={readiness.state}>
                  {t(...READINESS_WORDS[readiness.state])}
                </span>{" "}
                {t("Required items", "必要項")} {readiness.required_satisfied} /{" "}
                {readiness.required_total}
              </p>
              {dedupBlockers(readiness.blockers).length ? (
                <div className="notice" data-tone="warn">
                  <strong>{t("Items blocking submission", "尚未齊備的必要項")}</strong>
                  <ul>
                    {dedupBlockers(readiness.blockers).map((blocker) => (
                      <li key={`${blocker.code}::${blocker.source_key ?? ""}`}>
                        {blocker.message}
                        <br />
                        <span className="small muted">
                          {t("Needed", "需要")}：{blocker.needed} · {t("Action", "處理方式")}：
                          {blocker.action}
                        </span>
                        <details>
                          <summary>{t("Technical reference", "技術參考")}</summary>
                          <dl className="kv">
                            <dt>code</dt>
                            <dd>
                              <code>{blocker.code}</code>
                            </dd>
                            {blocker.source_key ? (
                              <>
                                <dt>source_key</dt>
                                <dd>
                                  <code>{blocker.source_key}</code>
                                </dd>
                              </>
                            ) : null}
                            {blocker.table ? (
                              <>
                                <dt>table</dt>
                                <dd>
                                  <code>{blocker.table}</code>
                                </dd>
                              </>
                            ) : null}
                            {blocker.subject_id ? (
                              <>
                                <dt>subject_id</dt>
                                <dd>
                                  <code>{blocker.subject_id}</code>
                                </dd>
                              </>
                            ) : null}
                            {blocker.current_state ? (
                              <>
                                <dt>current_state</dt>
                                <dd>
                                  <code>{blocker.current_state}</code>
                                </dd>
                              </>
                            ) : null}
                          </dl>
                        </details>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <p className="small muted">
                {t("Readiness policy version", "就緒判定政策版本")}：
                <code>{readiness.policy_version}</code>
              </p>
            </div>
          ) : (
            <p className="notice" data-tone="warn" role="status">
              {t(
                "The service did not supply readiness for this content, so submission cannot be judged here.",
                "服務未提供送核就緒資訊，無法在此判定可否送核。",
              )}
            </p>
          )}
          <button
            type="button"
            data-variant="primary"
            disabled={busy || !canSubmit}
            onClick={() => {
              void submit();
            }}
          >
            {t("Submit approval request", "送出核准申請")}
            <Icon name="arrow" />
          </button>
          {!canSubmit && blockedReason ? (
            <p className="small muted" role="status">
              {t("Cannot submit now", "目前無法送出")}：{blockedReason}
            </p>
          ) : null}
          {approval ? (
            <div aria-label={t("Current approval request", "目前核准申請")}>
              <p role="status" aria-live="polite">
                <span className="status-pill" data-status={approval.status}>
                  {approvalStatusText(approval.status, t)}
                </span>
              </p>
              <dl className="kv">
                <dt>{t("Submitted by", "送核者")}</dt>
                <dd>
                  {approval.submitted_by.actor_id}（{approval.submitted_by.kind}）
                </dd>
                <dt>{t("Submitted at", "送核時間")}</dt>
                <dd>{formatServerSeconds(approval.submitted_at)} · Asia/Taipei</dd>
                {approval.decision ? (
                  <>
                    <dt>{t("Decided at", "決定時間")}</dt>
                    <dd>
                      {formatServerSeconds(approval.decision.decided_at)} ·{" "}
                      {approval.decision.actor.actor_id}
                    </dd>
                  </>
                ) : null}
              </dl>
              {(approval.status === "returned" || approval.status === "withdrawn") &&
              approval.decision?.reason ? (
                <p className="notice" data-tone="warn" role="status">
                  {t("Reason", "理由")}：{approval.decision.reason}
                </p>
              ) : null}
              {decisionActions.length > 0 ? (
                activeDecision === null ? (
                  <div className="toolbar">
                    {decisionActions.map((kind) => (
                      <button
                        key={kind}
                        type="button"
                        data-variant={kind === "approve" ? "primary" : undefined}
                        disabled={busy}
                        onClick={() => setPendingDecision(kind)}
                      >
                        {t(...decisionWords[kind])}
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="notice" data-tone="warn">
                    <p style={{ margin: 0 }}>
                      {t("About to record the decision", "即將記錄決定")}：
                      {t(...decisionWords[activeDecision])}。
                      {t("This is recorded by the service.", "此決定將由服務留存。")}
                    </p>
                    {activeDecision !== "approve" ? (
                      <>
                        <label>
                          {t("Reason (required)", "理由（必填）")}
                          <textarea
                            value={reason}
                            rows={2}
                            onChange={(event) => setReason(event.target.value)}
                          />
                        </label>
                        {!reason.trim() ? (
                          <p className="small muted">
                            {t(
                              "Return and withdraw both require a reason.",
                              "退回與撤回皆須填寫理由。",
                            )}
                          </p>
                        ) : null}
                      </>
                    ) : null}
                    <button
                      type="button"
                      data-variant="primary"
                      disabled={busy || (activeDecision !== "approve" && !reason.trim())}
                      onClick={() => {
                        void decide(activeDecision);
                      }}
                    >
                      {t("Confirm decision", "確認送出決定")}
                    </button>{" "}
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setPendingDecision(null);
                        setReason("");
                      }}
                    >
                      {t("Cancel", "取消")}
                    </button>
                  </div>
                )
              ) : null}
              <details>
                <summary>
                  {t("Technical reference (binding digests)", "技術參考（綁定雜湊）")}
                </summary>
                <dl className="kv">
                  <dt>{t("Approval id", "核准申請識別碼")}</dt>
                  <dd>
                    <code>{approval.approval_id}</code>
                  </dd>
                  <dt>{t("Calculation snapshot digest", "計算快照雜湊")}</dt>
                  <dd>
                    <code>{approval.binding.calculation_snapshot_digest}</code>
                  </dd>
                  <dt>{t("Template bundle", "範本套件")}</dt>
                  <dd>
                    <code>{approval.binding.template_bundle.bundle_id}</code> ·{" "}
                    <code>{approval.binding.template_bundle.version}</code> ·{" "}
                    <code>{approval.binding.template_bundle.bundle_hash}</code>
                  </dd>
                  {(["table_3", "table_4", "table_5"] as const).map((table) => (
                    <ApprovalHash
                      key={table}
                      table={table}
                      hash={approval.binding.workbook_hashes[table]}
                    />
                  ))}
                  <dt>{t("Readiness policy version", "就緒判定政策版本")}</dt>
                  <dd>
                    <code>{approval.binding.readiness_policy_version}</code>
                  </dd>
                  <dt>{t("Payload digest", "申請內容雜湊")}</dt>
                  <dd>
                    <code>{approval.payload_digest}</code>
                  </dd>
                </dl>
              </details>
            </div>
          ) : (
            <p className="small muted">
              {t(
                "No approval request exists for the current content yet.",
                "目前內容尚無核准申請紀錄。",
              )}
            </p>
          )}
          {notice ? <ApprovalNotice notice={notice} pending={pending} retry={submit} /> : null}
        </>
      )}
    </section>
  );
}

function ApprovalHash({ table, hash }: { table: string; hash: string }) {
  return (
    <>
      <dt>
        <code>{table}</code>
      </dt>
      <dd>
        <code>{hash}</code>
      </dd>
    </>
  );
}

function ApprovalNotice({
  notice,
  pending,
  retry,
}: {
  notice: PanelNotice;
  pending: SubmitAttempt | null;
  retry: () => Promise<void>;
}) {
  const t = useText();
  if (notice.kind === "unknown-submit") {
    return (
      <div className="notice" data-tone="warn" role="alert">
        <p style={{ margin: 0 }}>
          {t(
            "It is unknown whether the approval request was recorded. Retry sends the same request and key, so a replay returns the original request instead of creating a second one.",
            "無法確認核准申請是否已被受理。重試會沿用同一組請求與識別碼，若服務已受理將回到原本的申請，不會另建第二份。",
          )}
        </p>
        {pending ? (
          <button
            type="button"
            onClick={() => {
              void retry();
            }}
          >
            {t("Retry the same request", "以同一請求重試")}
          </button>
        ) : null}
      </div>
    );
  }
  if (notice.kind === "unknown-decision") {
    return (
      <div className="notice" data-tone="warn" role="alert">
        <p style={{ margin: 0 }}>
          {t(
            "It is unknown whether the decision was recorded. Confirming again resends the same decision and key, so a replay returns the recorded decision instead of recording a second one.",
            "無法確認決定是否已被記錄。再次確認送出會沿用同一組決定與識別碼，若服務已記錄將回到原本的結果，不會重複記錄。",
          )}
        </p>
      </div>
    );
  }
  if (notice.kind === "key-unavailable") {
    return (
      <p className="notice" data-tone="danger" role="alert">
        {t(
          "An operation identifier could not be generated in this browser. Update the browser or open the workbench over a secure (HTTPS) connection, then try again. No request was sent.",
          "無法產生操作識別碼，請更新瀏覽器或改用安全連線（HTTPS）後再試。本次未送出任何請求。",
        )}
      </p>
    );
  }
  const { error, on } = notice;
  const decisionWords: Partial<Record<typeof error.code, [string, string]>> = {
    unauthorized: [
      "You do not have approval permission (publish_artifact).",
      "您沒有核准權限（publish_artifact）。",
    ],
    version_conflict: [
      "The request state changed first, or this decision already exists. Read the current state again.",
      "申請狀態已先被變更，或此決定已存在；請重新讀取目前狀態。",
    ],
  };
  const submitWords: Partial<Record<typeof error.code, [string, string]>> = {
    unauthorized: [
      "No permission, or the session has lapsed. Sign in again.",
      "無權限或工作階段已失效，請重新登入。",
    ],
    version_conflict: [
      "The content is not ready or has changed since this basis was read. Refresh, then submit again.",
      "內容尚未就緒或已變更，請重新讀取狀態後再送核。",
    ],
  };
  const shared: Partial<Record<typeof error.code, [string, string]>> = {
    not_found: [
      "This request or job is not available to this session.",
      "此申請或案件目前不在可存取範圍內。",
    ],
    capability_unavailable: [
      "The approval capability is not configured in this deployment.",
      "核准功能尚未配置，此面板僅呈現服務實際回覆。",
    ],
    invalid_request: [
      "The service rejected this request as malformed.",
      "服務未接受這份請求，請重新讀取狀態後再試。",
    ],
    execution_failed: [
      "The service could not complete this action. Read the current state again.",
      "服務未能完成此操作，請重新讀取目前狀態確認結果。",
    ],
  };
  const pair =
    (on === "decision" ? decisionWords[error.code] : submitWords[error.code]) ??
    shared[error.code] ??
    shared.execution_failed;
  return (
    <div className="notice" data-tone="danger" role="alert">
      <p style={{ margin: 0 }}>{pair ? t(...pair) : error.code}</p>
      {error.serviceMessage ? (
        <p className="small muted" style={{ margin: "0.25rem 0 0" }}>
          {t("Service message", "服務回覆")}：{error.serviceMessage}
        </p>
      ) : null}
    </div>
  );
}
