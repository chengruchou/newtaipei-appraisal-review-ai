import { useEffect, useState } from "react";
import { newIdempotencyKey } from "@/api/client";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";
import {
  CandidateServiceError,
  type CandidateApi,
  type CandidateConfirmation,
  type ConfirmCandidateCommand,
  type FactCandidate,
} from "./candidate-api";
import { fieldKeyLabel } from "./field-labels";

/**
 * 資料核對: externally fetched candidate values for the current case. Every value here is
 * a CANDIDATE only - the service adopts nothing until a named human with the confirmation
 * permission accepts the exact fetched value, unit, applicable date and evidence digest.
 * The panel therefore echoes the stored candidate verbatim in the confirm command and
 * never edits a value: a mismatch is the service's own 409, not something to paper over.
 */

type Load =
  | { name: "loading" }
  | { name: "ready"; candidates: FactCandidate[] }
  | { name: "failed"; code: string };

const STATUS_WORDS: Record<FactCandidate["status"], [string, string]> = {
  candidate: ["Candidate", "候選"],
  confirmed: ["Confirmed", "已確認"],
  rejected: ["Rejected", "已拒絕"],
  superseded: ["Superseded", "已被取代"],
};

interface DecisionPick {
  candidateId: string;
  decision: "accept" | "reject";
}

/** An unknown-outcome confirm; retrying replays exactly this candidate, key and payload. */
interface Attempt {
  key: string;
  candidateId: string;
  command: ConfirmCandidateCommand;
}

type Notice =
  | { kind: "service"; error: CandidateServiceError }
  | { kind: "unknown" }
  | { kind: "key-unavailable" };

function formatServerSeconds(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    hour12: false,
  });
}

function samePayload(a: ConfirmCandidateCommand, b: ConfirmCandidateCommand): boolean {
  return (
    JSON.stringify({ ...a, idempotency_key: "" }) === JSON.stringify({ ...b, idempotency_key: "" })
  );
}

function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

export function CandidatePanel({ api, caseId }: { api: CandidateApi; caseId: string }) {
  const t = useText();
  const [load, setLoad] = useState<Load>({ name: "loading" });
  const [generation, setGeneration] = useState(0);
  const reload = () => setGeneration((n) => n + 1);
  const [busy, setBusy] = useState(false);
  /** The service refused a decision for lack of permission; offer no more buttons. */
  const [confirmDenied, setConfirmDenied] = useState(false);
  const [receipts, setReceipts] = useState<Record<string, CandidateConfirmation>>({});
  const [pick, setPick] = useState<DecisionPick | null>(null);
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<Attempt | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoad({ name: "loading" });
    api
      .listCandidates(caseId)
      .then((list) => {
        if (cancelled) return;
        setLoad({ name: "ready", candidates: list.candidates });
        // A fresh list is the durable truth; drop local decision echoes.
        setReceipts({});
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setLoad({
            name: "failed",
            code: cause instanceof CandidateServiceError ? cause.code : "transport",
          });
      });
    return () => {
      cancelled = true;
    };
  }, [api, caseId, generation]);

  async function decide(candidate: FactCandidate, decision: "accept" | "reject") {
    if (busy || confirmDenied) return;
    const trimmed = reason.trim();
    if (decision === "reject" && !trimmed) return;
    // The command echoes the stored candidate exactly; the service refuses any other echo.
    const draft: ConfirmCandidateCommand = {
      schema_version: "service-v1",
      idempotency_key: "",
      decision,
      ...(decision === "reject" ? { reason: trimmed } : {}),
      expected_revision: candidate.revision_id,
      accepted_value: candidate.value,
      accepted_unit: candidate.unit,
      accepted_applicable_date: candidate.applicable_date,
      evidence_sha256: candidate.evidence.sha256,
    };
    // An unknown outcome retries with the SAME key and payload, so a replay returns the
    // original receipt. Any other confirm mints a fresh key.
    const reuse =
      pending !== null &&
      pending.candidateId === candidate.candidate_id &&
      samePayload(pending.command, draft);
    let key: string;
    try {
      key = reuse && pending ? pending.key : newIdempotencyKey();
    } catch {
      setNotice({ kind: "key-unavailable" });
      return;
    }
    const command: ConfirmCandidateCommand = { ...draft, idempotency_key: key };
    setBusy(true);
    setNotice(null);
    try {
      const receipt = await api.confirmCandidate(caseId, candidate.candidate_id, command);
      setPending(null);
      setReceipts((known) => ({ ...known, [candidate.candidate_id]: receipt }));
      setPick(null);
      setReason("");
      reload();
    } catch (cause) {
      if (cause instanceof CandidateServiceError) {
        setPending(null);
        if (cause.code === "unauthorized") {
          setConfirmDenied(true);
          setPick(null);
          setReason("");
        }
        setNotice({ kind: "service", error: cause });
      } else {
        setPending({ key, candidateId: candidate.candidate_id, command });
        setNotice({ kind: "unknown" });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-label={t("Data verification", "資料核對")}>
      <h2>
        <Icon name="check" />
        {t("Data verification (external candidates)", "資料核對（外部候選值）")}
      </h2>
      <p className="small muted">
        {t(
          "Values fetched from official open data are candidates only. They are adopted only after a person with confirmation permission accepts the exact value.",
          "來自官方開放資料的擷取值僅為候選；須由具確認權限的人員逐筆確認後才會採用。",
        )}
      </p>
      {load.name === "loading" ? (
        <p role="status" className="muted">
          {t("Reading candidate values…", "正在讀取候選資料…")}
        </p>
      ) : load.name === "failed" ? (
        <div className="notice" data-tone="warn" role="alert">
          <p style={{ margin: 0 }}>{listProblemText(load.code, t)}</p>
          <button type="button" disabled={busy} onClick={reload}>
            {t("Read again", "重新讀取")}
          </button>
        </div>
      ) : load.candidates.length === 0 ? (
        <p className="empty-state">
          {t("No external candidate data exists for this case yet.", "尚無外部候選資料。")}
        </p>
      ) : (
        <ul className="plain">
          {load.candidates.map((candidate) => {
            const receipt = receipts[candidate.candidate_id] ?? null;
            const shownStatus: FactCandidate["status"] = receipt
              ? receipt.decision === "accept"
                ? "confirmed"
                : "rejected"
              : candidate.status;
            const active =
              pick !== null && pick.candidateId === candidate.candidate_id ? pick : null;
            return (
              <li className="blocker-row" key={candidate.candidate_id}>
                <div className="blocker-head">
                  <strong>{fieldKeyLabel(candidate.field_key, t)}</strong>
                  <span className="status-pill" data-status={shownStatus}>
                    {t(...STATUS_WORDS[shownStatus])}
                  </span>
                </div>
                <dl className="kv">
                  <dt>{t("Candidate value", "候選值")}</dt>
                  <dd>
                    <strong>{candidate.value}</strong>
                    {candidate.unit ? ` ${candidate.unit}` : ""}
                  </dd>
                  <dt>{t("Applicable date", "適用日期")}</dt>
                  <dd>{candidate.applicable_date ?? t("Not supplied", "未提供")}</dd>
                  <dt>{t("Source", "來源")}</dt>
                  <dd>
                    {candidate.source_id}
                    {isHttpUrl(candidate.evidence.url) ? (
                      <>
                        {" · "}
                        <a href={candidate.evidence.url} target="_blank" rel="noopener noreferrer">
                          {t("Open source page", "查看來源頁面")}
                        </a>
                      </>
                    ) : null}
                  </dd>
                  <dt>{t("Retrieved at", "取得時間")}</dt>
                  <dd>{formatServerSeconds(candidate.evidence.retrieved_at)} · Asia/Taipei</dd>
                </dl>
                {receipt ? (
                  <p role="status">
                    {receipt.decision === "accept"
                      ? t("Decision recorded: accepted", "已記錄決定：確認採用")
                      : t("Decision recorded: rejected", "已記錄決定：拒絕")}
                    （{formatServerSeconds(receipt.decided_at)}）
                  </p>
                ) : null}
                {candidate.status === "candidate" && !receipt ? (
                  confirmDenied ? (
                    <p className="small muted" role="status">
                      {t(
                        "Confirming requires a session with confirmation permission.",
                        "需具確認權限的登入，此工作階段僅能檢視候選資料。",
                      )}
                    </p>
                  ) : active === null ? (
                    <div className="toolbar">
                      <button
                        type="button"
                        data-variant="primary"
                        disabled={busy}
                        onClick={() =>
                          setPick({ candidateId: candidate.candidate_id, decision: "accept" })
                        }
                      >
                        {t("Accept this value", "確認採用")}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          setPick({ candidateId: candidate.candidate_id, decision: "reject" })
                        }
                      >
                        {t("Reject", "拒絕")}
                      </button>
                    </div>
                  ) : (
                    <div className="notice" data-tone="warn">
                      <p style={{ margin: 0 }}>
                        {active.decision === "accept"
                          ? t(
                              "About to accept exactly this value:",
                              "即將確認採用此候選值（以下內容不可修改）：",
                            )
                          : t("About to reject this candidate value.", "即將拒絕此候選值。")}{" "}
                        {active.decision === "accept" ? (
                          <strong>
                            {candidate.value}
                            {candidate.unit ? ` ${candidate.unit}` : ""}
                          </strong>
                        ) : null}
                      </p>
                      {active.decision === "reject" ? (
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
                              {t("A rejection requires a reason.", "拒絕須填寫理由。")}
                            </p>
                          ) : null}
                        </>
                      ) : null}
                      <button
                        type="button"
                        data-variant="primary"
                        disabled={busy || (active.decision === "reject" && !reason.trim())}
                        onClick={() => {
                          void decide(candidate, active.decision);
                        }}
                      >
                        {t("Confirm decision", "確認送出決定")}
                      </button>{" "}
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setPick(null);
                          setReason("");
                        }}
                      >
                        {t("Cancel", "取消")}
                      </button>
                    </div>
                  )
                ) : null}
                <details>
                  <summary>{t("Technical record", "技術紀錄")}</summary>
                  <dl className="kv">
                    <dt>candidate_id</dt>
                    <dd>
                      <code>{candidate.candidate_id}</code>
                    </dd>
                    <dt>subject_id</dt>
                    <dd>
                      <code>{candidate.subject_id}</code>
                    </dd>
                    <dt>field_key</dt>
                    <dd>
                      <code>{candidate.field_key}</code>
                    </dd>
                    <dt>revision_id</dt>
                    <dd>
                      <code>{candidate.revision_id}</code>
                    </dd>
                    <dt>{t("Evidence SHA-256", "證據雜湊（SHA-256）")}</dt>
                    <dd>
                      <code>{candidate.evidence.sha256}</code>
                    </dd>
                    {candidate.evidence.row_locator ? (
                      <>
                        <dt>row_locator</dt>
                        <dd>
                          <code>{candidate.evidence.row_locator}</code>
                        </dd>
                      </>
                    ) : null}
                    {candidate.evidence.excerpt ? (
                      <>
                        <dt>{t("Excerpt", "來源摘錄")}</dt>
                        <dd>{candidate.evidence.excerpt}</dd>
                      </>
                    ) : null}
                  </dl>
                </details>
              </li>
            );
          })}
        </ul>
      )}
      {notice ? <CandidateNotice notice={notice} onRefresh={reload} busy={busy} /> : null}
    </section>
  );
}

function listProblemText(code: string, t: (english: string, chinese: string) => string): string {
  const words: Record<string, [string, string]> = {
    unauthorized: [
      "This session may not read candidate data for this case.",
      "目前無權讀取此案件的候選資料。",
    ],
    not_found: [
      "This case is not available to the candidate data service.",
      "此案件不在候選資料服務範圍內。",
    ],
    version_conflict: ["The data changed while reading. Read again.", "資料已變更，請重新整理。"],
    capability_unavailable: [
      "Candidate data is not configured in this deployment. This panel shows only what the service actually answers.",
      "候選資料功能尚未配置；此面板僅呈現服務實際回覆。",
    ],
    invalid_request: [
      "The service did not accept this read. Read again after refreshing the case.",
      "服務未接受這次查詢，請重新整理後再試。",
    ],
    execution_failed: [
      "The service could not complete the read. Try again shortly.",
      "服務未能完成讀取，請稍後再試。",
    ],
    transport: [
      "Could not reach the candidate data service.",
      "無法連線候選資料服務，請稍後重試。",
    ],
  };
  return t(...(words[code] ?? words.transport!));
}

function CandidateNotice({
  notice,
  onRefresh,
  busy,
}: {
  notice: Notice;
  onRefresh: () => void;
  busy: boolean;
}) {
  const t = useText();
  if (notice.kind === "unknown") {
    return (
      <div className="notice" data-tone="warn" role="alert">
        <p style={{ margin: 0 }}>
          {t(
            "It is unknown whether the decision was recorded. Confirming again resends the same decision and key, so a replay returns the recorded receipt instead of recording a second one.",
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
  const { error } = notice;
  const words: Partial<Record<typeof error.code, [string, string]>> = {
    unauthorized: [
      "Confirming requires a session with confirmation permission. No decision was recorded.",
      "需具確認權限的登入，這次決定未被記錄。",
    ],
    version_conflict: [
      "The data has changed. Refresh, review the current candidate, then decide again.",
      "資料已變更，請重新整理後再核對目前候選值。",
    ],
    not_found: [
      "This candidate is no longer available to this session.",
      "此候選資料目前不在可存取範圍內。",
    ],
    invalid_request: [
      "The service rejected this decision as malformed. Refresh and try again.",
      "服務未接受這份決定，請重新整理後再試。",
    ],
    capability_unavailable: [
      "Candidate confirmation is not configured in this deployment.",
      "候選確認功能尚未配置；此面板僅呈現服務實際回覆。",
    ],
    execution_failed: [
      "The service could not complete this decision. Read the current state again.",
      "服務未能完成此決定，請重新讀取目前狀態確認結果。",
    ],
  };
  const pair = words[error.code] ?? words.execution_failed;
  return (
    <div className="notice" data-tone="danger" role="alert">
      <p style={{ margin: 0 }}>{pair ? t(...pair) : error.code}</p>
      {error.code === "version_conflict" ? (
        <button type="button" disabled={busy} onClick={onRefresh}>
          {t("Refresh candidates", "重新整理候選資料")}
        </button>
      ) : null}
      {error.serviceMessage ? (
        <details>
          <summary>{t("Diagnostics", "診斷資訊")}</summary>
          <p className="small muted" style={{ margin: "0.25rem 0 0" }}>
            {error.serviceMessage}
          </p>
        </details>
      ) : null}
    </div>
  );
}
