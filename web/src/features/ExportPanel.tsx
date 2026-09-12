import { useEffect, useState } from "react";
import { newIdempotencyKey } from "@/api/client";
import {
  ExportServiceError,
  type CreateExportCommand,
  type ExportArtifact,
  type ExportFormat,
  type ExportMode,
  type ExportOperation,
  type ExportRunReference,
  type ExportsApi,
  type ExportTableDelivery,
  type TemplateBundleReference,
} from "@/api/exports";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";

/**
 * What one export request is computed from. The page supplies it from already-authorized
 * job state; the panel never invents a run, digest or bundle identity of its own.
 */
export interface ExportBasis {
  run: ExportRunReference;
  calculationSnapshotDigest: string;
  templateBundle: TemplateBundleReference;
}

const TERMINAL: readonly ExportOperation["status"][] = ["succeeded", "failed", "partial"];

const TABLE_WORDS: Record<ExportTableDelivery["table"], [string, string]> = {
  table_3: ["Form 3 · Land value section survey", "表3 地價區段勘查表"],
  table_4: ["Form 4 · Sales comparison appraisal", "表4 比較法調查估價表"],
  table_5: ["Form 5 · Regional factor analysis", "表5 影響地價區域因素分析明細表"],
};

interface Attempt {
  key: string;
  command: CreateExportCommand;
}

type Notice =
  { kind: "service"; error: ExportServiceError } | { kind: "unknown" } | { kind: "poll-failed" };

function samePayload(a: CreateExportCommand, b: CreateExportCommand): boolean {
  return (
    JSON.stringify({ ...a, idempotency_key: "" }) === JSON.stringify({ ...b, idempotency_key: "" })
  );
}

function DraftBadge() {
  const t = useText();
  return (
    <span className="status-pill" data-status="draft">
      {t("Draft (not formally approved)", "草稿（未正式核定）")}
    </span>
  );
}

export function ExportPanel({
  api,
  jobId,
  basis,
  pollIntervalMs = 2000,
}: {
  api: ExportsApi;
  jobId: string;
  basis: ExportBasis | null;
  pollIntervalMs?: number;
}) {
  const t = useText();
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [mode, setMode] = useState<ExportMode>("draft");
  const [busy, setBusy] = useState(false);
  /** The request whose POST outcome is unknown; a retry reuses exactly this key+payload. */
  const [pending, setPending] = useState<Attempt | null>(null);
  const [operation, setOperation] = useState<ExportOperation | null>(null);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [downloadNote, setDownloadNote] = useState<{
    tone: "ok" | "danger";
    text: string;
    offerRefresh: boolean;
  } | null>(null);

  const exportId = operation ? operation.export_id : null;
  const terminal = operation !== null && TERMINAL.includes(operation.status);
  const halted = notice?.kind === "poll-failed" || (notice?.kind === "service" && !terminal);

  useEffect(() => {
    if (!exportId || terminal || halted) return;
    let active = true;
    let inFlight = false;
    const timer = setInterval(() => {
      if (inFlight || !active) return;
      inFlight = true;
      api
        .readExport(jobId, exportId)
        .then((next) => {
          if (active) setOperation(next);
        })
        .catch((cause: unknown) => {
          if (!active) return;
          setNotice(
            cause instanceof ExportServiceError
              ? { kind: "service", error: cause }
              : { kind: "poll-failed" },
          );
        })
        .finally(() => {
          inFlight = false;
        });
    }, pollIntervalMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api, jobId, exportId, terminal, halted, pollIntervalMs]);

  function buildPayload(key: string): CreateExportCommand {
    if (!basis) throw new Error("Export basis is not available");
    return {
      schema_version: "service-v1",
      idempotency_key: key,
      run: basis.run,
      calculation_snapshot_digest: basis.calculationSnapshotDigest,
      template_bundle: basis.templateBundle,
      requested_mode: mode,
      export_format: format,
    };
  }

  async function generate() {
    if (!basis || busy) return;
    const draft = buildPayload("");
    // An unknown outcome is retried with the SAME key and payload so a replay returns the
    // original operation. Anything else - including a changed format or mode - is a new
    // operation under a new key; an existing operation's format never mutates.
    const reuse = pending !== null && samePayload(pending.command, draft);
    const key = reuse && pending ? pending.key : newIdempotencyKey();
    const command: CreateExportCommand = { ...draft, idempotency_key: key };
    setBusy(true);
    setNotice(null);
    setDownloadNote(null);
    setAttempt({ key, command });
    try {
      const accepted = await api.createExport(jobId, command);
      setPending(null);
      setOperation(accepted);
    } catch (cause) {
      if (cause instanceof ExportServiceError) {
        // A definitive refusal: this exact request will not be accepted, so the key is spent.
        setPending(null);
        setNotice({ kind: "service", error: cause });
      } else {
        setPending({ key, command });
        setNotice({ kind: "unknown" });
      }
    } finally {
      setBusy(false);
    }
  }

  async function refreshOperation() {
    if (!exportId || busy) return;
    setBusy(true);
    try {
      setOperation(await api.readExport(jobId, exportId));
      setNotice(null);
      setDownloadNote(null);
    } catch (cause) {
      setNotice(
        cause instanceof ExportServiceError
          ? { kind: "service", error: cause }
          : { kind: "poll-failed" },
      );
    } finally {
      setBusy(false);
    }
  }

  async function download(artifact: ExportArtifact) {
    if (busy) return;
    setBusy(true);
    setDownloadNote(null);
    try {
      const file = await api.downloadArtifact(jobId, artifact);
      const url = URL.createObjectURL(new Blob([file.bytes], { type: file.contentType }));
      const link = document.createElement("a");
      link.href = url;
      link.download = file.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setDownloadNote({
        tone: "ok",
        text: t(`Downloaded ${file.filename}.`, `已下載 ${file.filename}。`),
        offerRefresh: false,
      });
    } catch (cause) {
      if (cause instanceof ExportServiceError && cause.code === "unauthorized") {
        setDownloadNote({
          tone: "danger",
          text: t(
            "The download authorization has lapsed. Re-authorize, then try again.",
            "下載授權已過期，請重新授權後再試。",
          ),
          offerRefresh: true,
        });
      } else {
        setDownloadNote({
          tone: "danger",
          text: t(
            "The file could not be downloaded or verified. No file was saved.",
            "檔案未能完成下載或驗證，未儲存任何檔案。",
          ),
          offerRefresh: true,
        });
      }
    } finally {
      setBusy(false);
    }
  }

  const inProgress = operation !== null && !terminal;
  const draft = operation !== null && operation.effective_mode === "draft";
  const artifactById = new Map((operation?.artifacts ?? []).map((a) => [a.artifact_id, a]));
  const missing = (operation?.tables ?? []).filter((row) => !row.delivered);
  const blockers = operation ? [...new Set(operation.blockers)] : [];

  return (
    <section className="panel" aria-label={t("Report export", "報表匯出")}>
      <h2>
        {t("Report export", "報表匯出")} {draft ? <DraftBadge /> : null}
      </h2>
      <p className="small muted">
        {t(
          "The service produces the official tables from the committed calculation snapshot. Nothing here recomputes a value.",
          "由服務依已提交的計算快照產出官方表格；此面板不重新計算任何數值。",
        )}
      </p>
      {!basis ? (
        <p className="notice" data-tone="warn" role="status">
          {t(
            "The current job state does not yet carry the run and bundle identity an export request requires.",
            "目前狀態尚未提供匯出所需的執行輪次與範本版本資訊，暫時無法建立匯出。",
          )}
        </p>
      ) : (
        <>
          <fieldset disabled={busy}>
            <legend>{t("File format", "檔案格式")}</legend>
            <label>
              <input
                type="radio"
                name="export-format"
                value="xlsx"
                checked={format === "xlsx"}
                onChange={() => setFormat("xlsx")}
              />{" "}
              Excel (.xlsx)
            </label>
            <label>
              <input
                type="radio"
                name="export-format"
                value="pdf"
                checked={format === "pdf"}
                onChange={() => setFormat("pdf")}
              />{" "}
              PDF (.pdf)
            </label>
            <p className="small muted">
              {t(
                "Switching format starts a new export operation under a new key; an existing operation keeps its format.",
                "切換格式會以新的識別碼建立新的匯出作業；既有作業的格式不會改變。",
              )}
            </p>
          </fieldset>
          <fieldset disabled={busy}>
            <legend>{t("Requested mode", "申請版本")}</legend>
            <label>
              <input
                type="radio"
                name="export-mode"
                value="draft"
                checked={mode === "draft"}
                onChange={() => setMode("draft")}
              />{" "}
              {t("Draft", "草稿")}
            </label>
            <label>
              <input
                type="radio"
                name="export-mode"
                value="formal"
                checked={mode === "formal"}
                onChange={() => setMode("formal")}
              />{" "}
              {t("Formal (only if unblocked)", "正式（僅於無阻擋時）")}
            </label>
          </fieldset>
          <button
            type="button"
            data-variant="primary"
            disabled={busy || inProgress}
            onClick={() => {
              void generate();
            }}
          >
            {t("Generate and download", "產生並下載")}
            <Icon name="arrow" />
          </button>
          {notice ? <NoticeView notice={notice} pending={pending} retry={generate} /> : null}
          {operation ? (
            <div aria-label={t("Export operation status", "匯出作業狀態")}>
              <p role="status" aria-live="polite">
                <span className="status-pill" data-status={operation.status}>
                  {statusWords(operation.status, t)}
                </span>{" "}
                {inProgress
                  ? t(
                      "Reading the actual state from the service every 2 seconds.",
                      "每 2 秒向服務讀取實際狀態。",
                    )
                  : null}
              </p>
              {operation.status === "failed" ? (
                <p className="notice" data-tone="danger" role="alert">
                  {t("The export failed.", "匯出失敗。")}{" "}
                  {operation.problem?.message ??
                    operation.problem?.code ??
                    t("The service supplied no reason.", "服務未提供原因。")}
                </p>
              ) : null}
              {operation.status === "partial" ? (
                <div className="notice" data-tone="warn" role="alert">
                  <strong>{t("Partial delivery", "部分交付")}</strong>
                  <p style={{ margin: "0.25rem 0 0" }}>
                    {t("The tables below were not produced:", "以下表格未能產出，原因如各列所示：")}{" "}
                    {missing
                      .map(
                        (row) => `${t(...TABLE_WORDS[row.table])}（${reasonText(row.problem, t)}）`,
                      )
                      .join("；")}
                  </p>
                </div>
              ) : null}
              {terminal && operation.tables.length ? (
                <ul className="plain" aria-label={t("Official tables", "官方表格清單")}>
                  {operation.tables.map((row) => {
                    const artifact = row.artifact_id ? artifactById.get(row.artifact_id) : null;
                    return (
                      <li className="artifact-row" key={row.table}>
                        <span>
                          <Icon name="file" />
                          {t(...TABLE_WORDS[row.table])}
                        </span>
                        {row.delivered && artifact ? (
                          <span>
                            {draft ? <DraftBadge /> : null}{" "}
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => {
                                void download(artifact);
                              }}
                            >
                              {t("Download", "下載")} {artifact.filename}
                            </button>
                          </span>
                        ) : row.delivered ? (
                          <span className="muted">
                            {t(
                              "Delivered, but no downloadable file was published.",
                              "已標記交付，但服務未發布可下載檔案。",
                            )}
                          </span>
                        ) : (
                          <span className="muted">
                            {t("Not delivered", "未交付")}：{reasonText(row.problem, t)}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : null}
              {blockers.length ? (
                <div className="notice" data-tone="warn">
                  <strong>{t("Outstanding blockers", "尚未解除的限制")}</strong>
                  <ul>
                    {blockers.map((blocker) => (
                      <li key={blocker}>{blocker}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {downloadNote ? (
                <p
                  className="notice"
                  data-tone={downloadNote.tone === "ok" ? "ok" : "danger"}
                  role="status"
                >
                  {downloadNote.text}{" "}
                  {downloadNote.offerRefresh ? (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        void refreshOperation();
                      }}
                    >
                      {t("Re-read export state", "重新讀取匯出狀態")}
                    </button>
                  ) : null}
                </p>
              ) : null}
              <details>
                <summary>
                  {t("Technical reference (codes and hashes)", "技術參考（識別碼與雜湊）")}
                </summary>
                <dl className="kv">
                  <dt>{t("Export operation", "匯出作業識別碼")}</dt>
                  <dd>
                    <code>{operation.export_id}</code>
                  </dd>
                  <dt>{t("Idempotency key", "冪等識別碼")}</dt>
                  <dd>
                    <code>{attempt?.key ?? t("Unknown", "未知")}</code>
                  </dd>
                  <dt>{t("Run", "執行輪次")}</dt>
                  <dd>
                    <code>{basis.run.run_id}</code> · <code>{basis.run.revision.revision_id}</code>
                  </dd>
                  <dt>{t("Calculation snapshot digest", "計算快照雜湊")}</dt>
                  <dd>
                    <code>{basis.calculationSnapshotDigest}</code>
                  </dd>
                  <dt>{t("Template bundle", "範本套件")}</dt>
                  <dd>
                    <code>{basis.templateBundle.bundle_id}</code> ·{" "}
                    <code>{basis.templateBundle.version}</code> ·{" "}
                    <code>{basis.templateBundle.bundle_hash}</code>
                  </dd>
                  {operation.artifacts.map((artifact) => (
                    <ArtifactHash artifact={artifact} key={artifact.artifact_id} />
                  ))}
                </dl>
              </details>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function ArtifactHash({ artifact }: { artifact: ExportArtifact }) {
  return (
    <>
      <dt>
        <code>{artifact.filename}</code>
      </dt>
      <dd>
        <code>{artifact.content_hash}</code>
      </dd>
    </>
  );
}

function NoticeView({
  notice,
  pending,
  retry,
}: {
  notice: Notice;
  pending: Attempt | null;
  retry: () => Promise<void>;
}) {
  const t = useText();
  if (notice.kind === "unknown") {
    return (
      <div className="notice" data-tone="warn" role="alert">
        <p style={{ margin: 0 }}>
          {t(
            "It is unknown whether the export request was recorded. Retry sends the same request and key, so a replay returns the original operation instead of creating a second one.",
            "無法確認匯出請求是否已被受理。重試會沿用同一組請求與識別碼，若服務已受理將回到原本的作業，不會另建第二份。",
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
  if (notice.kind === "poll-failed") {
    return (
      <p className="notice" data-tone="warn" role="alert">
        {t(
          "The current export state could not be read. The operation itself is unaffected; try reading again.",
          "目前無法讀取匯出狀態；作業本身不受影響，請稍後重新讀取。",
        )}
      </p>
    );
  }
  const { error } = notice;
  const words: Partial<Record<typeof error.code, [string, string]>> = {
    unauthorized: [
      "No permission, or the session has lapsed. Sign in again, or ask a reviewer with export permission.",
      "無權限或工作階段已失效：請重新登入，或由具備匯出權限的審查者執行。",
    ],
    version_conflict: [
      "The format or content of this request has changed since the original operation. Generate again to start a new operation under a new key.",
      "格式或內容已變更，請重新產生：再次按「產生並下載」會以新的識別碼建立新的匯出。",
    ],
    capability_unavailable: [
      "The export capability is not configured in this deployment. This panel shows only what the service actually answers.",
      "匯出功能尚未配置。此面板僅呈現服務實際回覆，不會模擬成功。",
    ],
    not_found: [
      "This export or job is not available to this session.",
      "此匯出或案件目前不在可存取範圍內。",
    ],
    invalid_request: [
      "The service rejected the export request as malformed.",
      "服務未接受這份匯出請求，請重新讀取案件狀態後再試。",
    ],
    execution_failed: [
      "The service could not complete the export. Try again shortly.",
      "服務無法完成匯出，請稍後再試。",
    ],
  };
  const pair = words[error.code] ?? words.execution_failed;
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

function reasonText(
  problem: ExportTableDelivery["problem"],
  t: (english: string, chinese: string) => string,
): string {
  return (
    problem?.message ?? problem?.code ?? t("The service supplied no reason.", "服務未提供原因")
  );
}

function statusWords(
  status: ExportOperation["status"],
  t: (english: string, chinese: string) => string,
): string {
  const words: Record<ExportOperation["status"], [string, string]> = {
    queued: ["Queued", "已排入佇列"],
    running: ["Producing", "產生中"],
    succeeded: ["Completed", "已完成"],
    failed: ["Failed", "失敗"],
    partial: ["Partially delivered", "部分交付"],
  };
  return t(...words[status]);
}
