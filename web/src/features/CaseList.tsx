import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  IntakeRequestError,
  MATERIAL_SIZE_LIMIT_BYTES,
  newIdempotencyKey,
  type AuthSessionView,
  type CaseContextView,
  type CaseRecord,
  type CaseReviewBasis,
  type JobStatusView,
  type MaterialRecord,
  type ReviewClient,
  type ReviewSessionView,
} from "@/api/client";
import { ServiceError } from "@/api/problems";
import { sha256Hex } from "@/api/exports";
import { OperationKeySourceError } from "@/api/ids";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";
import { statusText } from "./workbench-state";
import { formatBytes, formatExpiry, readFileBytes, shortDisplayId, truncateMiddle } from "./intake";

const ellipsis: React.CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  maxWidth: "100%",
  display: "inline-block",
  verticalAlign: "bottom",
};

type UploadNote = { tone: "ok" | "warn" | "danger" | "neutral"; text: string; sha?: string };

/**
 * The signed-in case list: who the session is, the cases this session may open
 * (configured demo jobs stay clearly labeled as synthetic), creating a new case, and
 * uploading its materials. Every state shown here is a service answer, never a
 * frontend assumption.
 */
export function CaseList({
  client,
  session,
  recent,
  logout,
}: {
  client: ReviewClient;
  session: ReviewSessionView;
  recent: Map<string, string>;
  logout: () => void;
}) {
  const t = useText();
  const navigate = useNavigate();
  const [reference, setReference] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [rows, setRows] = useState<
    Array<{ id: string; context: CaseContextView; status: JobStatusView }>
  >([]);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [authSession, setAuthSession] = useState<AuthSessionView | null>(null);
  const [authSessionMissing, setAuthSessionMissing] = useState(false);
  const ids = JSON.stringify([
    ...new Set([...session.configured_jobs.map((job) => job.job_id), ...recent.keys()]),
  ]);
  const synthetic = session.data_mode === "synthetic";

  useEffect(() => {
    let active = true;
    client
      .readAuthSession()
      .then((view) => {
        if (active) setAuthSession(view);
      })
      .catch(() => {
        if (active) setAuthSessionMissing(true);
      });
    return () => {
      active = false;
    };
  }, [client]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setRows([]);
    setFailed(false);
    const known = JSON.parse(ids) as string[];
    void Promise.all(
      known.map(async (id) => ({
        id,
        context: await client.readCaseContext(id),
        status: await client.readJob(id),
      })),
    )
      .then((values) => {
        if (active) setRows(values);
      })
      .catch(() => {
        if (active) {
          setRows([]);
          setFailed(true);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [client, ids, reload]);

  const shown = rows.filter((row) => {
    const matchesSearch = JSON.stringify([row.context.identity, row.id])
      .toLocaleLowerCase()
      .includes(search.toLocaleLowerCase());
    const status = row.status.job_status;
    return (
      matchesSearch &&
      (filter === "all" ||
        (filter === "waiting" && status === "waiting_for_human") ||
        (filter === "running" && ["queued", "dispatched", "running"].includes(status)) ||
        (filter === "finished" && status === "succeeded"))
    );
  });

  const actorId = authSession?.actor_id ?? session.actor.actor_id;
  const expiry = formatExpiry(authSession?.expires_at ?? null);

  return (
    <article>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t("REVIEW WORKSPACE", "案件審查工作台")}</span>
          <h1>{t("Case list", "案件清單")}</h1>
          <p className="lead">
            {t(
              "Open an authorized case, or create a new one and upload its materials.",
              "開啟已授權案件，或建立新案件並上傳資料。",
            )}
          </p>
        </div>
      </div>
      <section className="panel soft" aria-label={t("Current session", "目前工作階段")}>
        <div className="panel-heading">
          <h2>
            <Icon name="person" />
            {t("Signed-in identity", "登入身分")}
          </h2>
          <button onClick={logout}>
            {t("Sign out of this session", "登出並清除本機工作階段")}
          </button>
        </div>
        <p>
          <span style={ellipsis} title={actorId}>
            {truncateMiddle(actorId, 40)}
          </span>
          {authSession?.kind ? <span className="muted"> · {authSession.kind}</span> : null}
        </p>
        <p className="small muted">
          {expiry
            ? `${t("Session expires at", "工作階段到期時間")}：${expiry}`
            : authSessionMissing
              ? t(
                  "Session details are not available from the service right now.",
                  "服務目前未提供工作階段到期資訊。",
                )
              : t("Expiry not provided.", "到期時間尚未提供。")}
        </p>
      </section>
      <section className="panel" aria-label={t("Authorized cases", "已授權案件")}>
        <div className="panel-heading">
          <h2>{t("Authorized cases", "已授權案件")}</h2>
          <button onClick={() => setReload((n) => n + 1)}>{t("Refresh list", "更新清單")}</button>
        </div>
        <div className="toolbar">
          <label className="search-box">
            <Icon name="search" />
            <span className="sr-only">{t("Search cases", "搜尋案件")}</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("District, use or case identifier", "搜尋行政區、用途或案件識別碼")}
            />
          </label>
          <div className="filters" aria-label={t("Filter cases", "篩選案件")}>
            {[
              ["all", t("All", "全部")],
              ["waiting", t("Needs attention", "等待人工")],
              ["running", t("Running", "執行中")],
              ["finished", t("Execution finished", "執行成功")],
            ].map(([key, label]) => (
              <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key!)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        {loading ? (
          <p role="status">{t("Checking current case access…", "正在核對目前案件存取權…")}</p>
        ) : failed ? (
          <p role="alert" className="notice" data-tone="warn">
            {t(
              "This list could not be authorized or loaded. It is not an empty-case result. Refresh after checking access.",
              "清單目前無法載入或授權，不能視為零案件。請確認服務與權限後重新更新。",
            )}
          </p>
        ) : shown.length === 0 ? (
          <div className="empty-state">
            <Icon name="file" />
            <h3>{t("No authorized case in this view", "目前檢視沒有已授權案件")}</h3>
            <p>
              {t(
                "Create a new case below, or adjust the filter.",
                "可於下方建立新案件，或調整篩選條件。",
              )}
            </p>
          </div>
        ) : (
          <div className="case-list">
            {shown.map((row) => (
              <div className="case-row" key={row.id}>
                <span className="round-icon">
                  <Icon name="file" />
                </span>
                <div className="case-name">
                  <h3>
                    {synthetic
                      ? `${t("Demo case", "示範案件")} ${shortDisplayId(row.context.job.case_id)}`
                      : `${
                          row.context.identity?.district ||
                          t("District not available", "行政區尚未提供")
                        } · ${
                          row.context.identity?.land_use_category ||
                          t("Use not available", "用途尚未提供")
                        }`}
                  </h3>
                  <span className="muted">
                    {synthetic ? (
                      <span className="status-pill">{t("Synthetic demo", "合成示範")}</span>
                    ) : null}{" "}
                    {row.context.identity?.district ||
                      t("District not available", "行政區尚未提供")}{" "}
                    ·{" "}
                    {row.context.identity?.effective_date ??
                      t("Date not available", "日期尚未提供")}
                  </span>
                  <details className="technical">
                    <summary>{t("Case reference", "案件參照")}</summary>
                    <code>{row.context.job.case_id}</code>
                  </details>
                </div>
                <span className="status-pill" data-status={row.status.job_status}>
                  {statusText(row.status.job_status, t)}
                </span>
                <Link className="button primary" to={`/jobs/${row.id}/progress`}>
                  {t("Open case", "開啟案件")}
                  <Icon name="arrow" />
                </Link>
              </div>
            ))}
          </div>
        )}
        <details className="technical">
          <summary>{t("Open by job reference (advanced)", "以工作參照開啟（進階）")}</summary>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (reference.trim())
                void navigate(`/jobs/${encodeURIComponent(reference.trim())}/progress`);
            }}
          >
            <label htmlFor="job">{t("Job identifier", "案件工作識別碼")}</label>
            <div className="input-action">
              <input
                id="job"
                value={reference}
                onChange={(event) => setReference(event.target.value)}
                autoComplete="off"
              />
              <button data-variant="primary" disabled={!reference.trim()}>
                {t("Open", "開啟")}
              </button>
            </div>
          </form>
        </details>
      </section>
      <CaseIntake client={client} />
    </article>
  );
}

/** Create a case, then upload and list its materials. */
function CaseIntake({ client }: { client: ReviewClient }) {
  const t = useText();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [district, setDistrict] = useState("");
  const [valuationDate, setValuationDate] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [createdCases, setCreatedCases] = useState<CaseRecord[]>([]);
  const [activeCase, setActiveCase] = useState<CaseRecord | null>(null);
  const [reviewable, setReviewable] = useState<CaseReviewBasis[]>([]);
  const [startingCase, setStartingCase] = useState<string | null>(null);
  const [startNote, setStartNote] = useState<string | null>(null);

  // Durable listing: a fresh sign-in finds the caller's own intake cases again.
  // A load failure keeps whatever was created in this session; it never claims
  // an empty account.
  useEffect(() => {
    let active = true;
    void client
      .listCases()
      .then((cases) => {
        if (!active) return;
        setCreatedCases((current) => {
          const merged = new Map(cases.map((record) => [record.case_id, record]));
          for (const record of current) merged.set(record.case_id, record);
          return [...merged.values()];
        });
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [client]);
  // Cases with admitted material this member may review. Separate from the intake
  // list above because these are not cases this account created; a load failure
  // leaves the section absent rather than claiming there are none.
  useEffect(() => {
    let active = true;
    void client
      .listReviewableCases()
      .then((cases) => {
        if (active) setReviewable(cases);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [client]);
  async function openReview(entry: CaseReviewBasis) {
    if (startingCase !== null) return;
    setStartingCase(entry.case_id);
    setStartNote(null);
    try {
      const started = await client.startReview(entry);
      await navigate(`/jobs/${started.job_id}/progress`);
    } catch (error) {
      setStartNote(
        error instanceof ServiceError && error.code === "unauthorized"
          ? t(
              "You do not have permission to review this case, or the session lapsed.",
              "沒有審查此案件的權限，或工作階段已失效，請重新登入。",
            )
          : t(
              "The review could not be opened. Nothing was created; try again.",
              "審查未能開啟，未建立任何資料，請再試一次。",
            ),
      );
    } finally {
      setStartingCase(null);
    }
  }
  const [materials, setMaterials] = useState<MaterialRecord[]>([]);
  const [materialsNote, setMaterialsNote] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadNote, setUploadNote] = useState<UploadNote | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  async function refreshMaterials(record: CaseRecord) {
    try {
      const view = await client.listCaseMaterials(record.case_id);
      setMaterials(view.materials);
      setMaterialsNote("");
    } catch {
      setMaterials([]);
      setMaterialsNote(
        t(
          "The material list could not be loaded right now; it is not proof of an empty case.",
          "材料清單目前無法載入，不能視為沒有材料。",
        ),
      );
    }
  }

  async function createCase() {
    const cleanTitle = title.trim();
    const cleanDistrict = district.trim();
    if (!cleanTitle || !cleanDistrict) {
      setCreateError(t("Title and district are required.", "請填寫案件名稱與行政區。"));
      return;
    }
    setCreating(true);
    setCreateError("");
    try {
      const record = await client.createCase({
        schema_version: "service-v1",
        idempotency_key: newIdempotencyKey(),
        title: cleanTitle,
        district: cleanDistrict,
        ...(valuationDate.trim() ? { valuation_date: valuationDate.trim() } : {}),
      });
      setCreatedCases((cases) => [record, ...cases.filter((c) => c.case_id !== record.case_id)]);
      setActiveCase(record);
      setTitle("");
      setDistrict("");
      setValuationDate("");
      setUploadNote(null);
      await refreshMaterials(record);
    } catch (cause) {
      setCreateError(describeIntakeFailure(cause, t));
    } finally {
      setCreating(false);
    }
  }

  async function uploadFile(file: File) {
    if (!activeCase) return;
    if (file.size > MATERIAL_SIZE_LIMIT_BYTES) {
      setUploadNote({
        tone: "danger",
        text: t(
          "This file exceeds the 64MB limit and was not uploaded.",
          "檔案超過 64MB 上限，未上傳。",
        ),
      });
      return;
    }
    setUploading(true);
    setUploadNote(null);
    try {
      const bytes = await readFileBytes(file);
      const record = await client.uploadCaseMaterial(activeCase.case_id, {
        bytes,
        filename: file.name,
        contentType: file.type || "application/octet-stream",
        idempotencyKey: newIdempotencyKey(),
      });
      const localHash = await sha256Hex(bytes);
      if (record.sha256.toLowerCase() === localHash.toLowerCase()) {
        setUploadNote({
          tone: "ok",
          text: t("Saved. SHA-256 verified.", "已保存，SHA-256 已核對。"),
          sha: record.sha256,
        });
      } else {
        setUploadNote({
          tone: "warn",
          text: t(
            "The service stored this file, but its SHA-256 does not match the local bytes. Do not rely on this material.",
            "服務已保存此檔案，但回報的 SHA-256 與本機計算不符，請勿採用此材料。",
          ),
          sha: record.sha256,
        });
      }
      await refreshMaterials(activeCase);
    } catch (cause) {
      setUploadNote({ tone: "danger", text: describeIntakeFailure(cause, t) });
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <div className="two-columns">
      <section className="panel" aria-label={t("Create a new case", "建立新案件")}>
        <h2>
          <Icon name="file" />
          {t("Create a new case", "建立新案件")}
        </h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!creating) void createCase();
          }}
        >
          <label htmlFor="case-title">{t("Case title", "案件名稱")}</label>
          <input
            id="case-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            disabled={creating}
            autoComplete="off"
          />
          <label htmlFor="case-district">{t("District", "行政區")}</label>
          <input
            id="case-district"
            value={district}
            onChange={(event) => setDistrict(event.target.value)}
            disabled={creating}
            autoComplete="off"
          />
          <label htmlFor="case-valuation-date">
            {t("Valuation date (optional)", "估價基準日（選填）")}
          </label>
          <input
            id="case-valuation-date"
            type="date"
            value={valuationDate}
            onChange={(event) => setValuationDate(event.target.value)}
            disabled={creating}
          />
          {createError ? (
            <p role="alert" className="notice" data-tone="danger">
              {createError}
            </p>
          ) : null}
          <div className="input-action">
            <button type="submit" data-variant="primary" disabled={creating}>
              {creating ? t("Creating…", "建立中…") : t("Create case", "建立案件")}
              <Icon name="arrow" />
            </button>
          </div>
        </form>
        {reviewable.length > 0 ? (
          <div className="case-list" style={{ marginTop: 16 }}>
            <h3>{t("Cases you can review", "可審查的案件")}</h3>
            <p className="small muted">
              {t(
                "These cases carry an admitted material revision. Opening one starts the review, or reopens it if it already exists.",
                "這些案件已有受控的材料版本。開啟即發起審查；若已存在則直接回到同一份審查。",
              )}
            </p>
            {reviewable.map((entry) => (
              <div className="case-row" key={entry.case_id}>
                <span className="round-icon">
                  <Icon name="check" />
                </span>
                <div className="case-name">
                  <h3 style={ellipsis}>
                    {t("Reviewable case", "可審查案件")} {shortDisplayId(entry.case_id)}
                  </h3>
                  <span className="muted">
                    {entry.documents.length} {t("admitted documents", "份受控文件")}
                  </span>
                  <details className="technical">
                    <summary>{t("Case reference", "案件參照")}</summary>
                    <code>{entry.case_id}</code>
                  </details>
                </div>
                <button
                  data-variant="primary"
                  disabled={startingCase !== null}
                  onClick={() => {
                    void openReview(entry);
                  }}
                >
                  {startingCase === entry.case_id
                    ? t("Opening…", "開啟中…")
                    : t("Open review", "開啟審查")}
                  <Icon name="arrow" />
                </button>
              </div>
            ))}
            {startNote ? (
              <p className="notice" data-tone="danger" role="alert">
                {startNote}
              </p>
            ) : null}
          </div>
        ) : null}
        {createdCases.length > 0 ? (
          <div className="case-list" style={{ marginTop: 16 }}>
            {createdCases.map((record) => (
              <div className="case-row" key={record.case_id}>
                <span className="round-icon">
                  <Icon name="file" />
                </span>
                <div className="case-name">
                  <h3 style={ellipsis} title={record.title}>
                    {record.title || t("Untitled case", "未命名案件")}
                  </h3>
                  <span className="muted">
                    {t("Display id", "顯示編號")} {shortDisplayId(record.case_id)} ·{" "}
                    {record.district || t("District not available", "行政區尚未提供")}
                  </span>
                  <details className="technical">
                    <summary>{t("Case reference", "案件參照")}</summary>
                    <code>{record.case_id}</code>
                  </details>
                </div>
                <button
                  data-variant="primary"
                  onClick={() => {
                    void navigate(`/cases/${record.case_id}`);
                  }}
                >
                  {t("Open case", "開啟案件")}
                  <Icon name="arrow" />
                </button>
                <button
                  aria-pressed={activeCase?.case_id === record.case_id}
                  onClick={() => {
                    setActiveCase(record);
                    setUploadNote(null);
                    void refreshMaterials(record);
                  }}
                >
                  {activeCase?.case_id === record.case_id
                    ? t("Selected", "管理中")
                    : t("Manage materials", "管理材料")}
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </section>
      <section className="panel" aria-label={t("Case materials", "案件材料")}>
        <h2>
          <Icon name="book" />
          {t("Case materials", "案件材料")}
        </h2>
        {activeCase ? (
          <>
            <p className="muted" style={ellipsis} title={activeCase.case_id}>
              {t("Case", "案件")}：{activeCase.title || shortDisplayId(activeCase.case_id)}（
              {t("Display id", "顯示編號")} {shortDisplayId(activeCase.case_id)}）
            </p>
            <label htmlFor="material-file">
              {t("Upload a material (max 64MB)", "上傳材料檔案（上限 64MB）")}
            </label>
            <input
              id="material-file"
              type="file"
              ref={fileInput}
              disabled={uploading}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void uploadFile(file);
              }}
            />
            {uploading ? (
              <p role="status">{t("Uploading and verifying…", "上傳並核對中…")}</p>
            ) : null}
            {uploadNote ? (
              <p
                role={uploadNote.tone === "ok" ? "status" : "alert"}
                className="notice"
                data-tone={uploadNote.tone}
              >
                {uploadNote.text}
                {uploadNote.sha ? (
                  <>
                    {" "}
                    <code title={uploadNote.sha}>SHA-256 {truncateMiddle(uploadNote.sha, 20)}</code>
                  </>
                ) : null}
              </p>
            ) : null}
            {materialsNote ? (
              <p role="alert" className="notice" data-tone="warn">
                {materialsNote}
              </p>
            ) : null}
            {materials.length === 0 && !materialsNote ? (
              <p className="small muted">
                {t("No stored material yet for this case.", "此案件目前沒有已保存的材料。")}
              </p>
            ) : (
              <ul className="document-list">
                {materials.map((material) => (
                  <li key={material.material_id}>
                    <div>
                      <span style={ellipsis} title={material.filename ?? material.material_id}>
                        {truncateMiddle(material.filename ?? material.material_id, 40)}
                      </span>
                      <span className="small muted">
                        {formatBytes(material.size) ?? t("Size not provided", "大小尚未提供")} ·{" "}
                        <code title={material.sha256}>
                          SHA-256 {truncateMiddle(material.sha256, 20)}
                        </code>
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="muted">
            {t(
              "Create or select a case on the left to upload its materials.",
              "請先於左側建立或選擇案件，再上傳材料。",
            )}
          </p>
        )}
      </section>
    </div>
  );
}

/** One honest sentence per failure class; never a raw JSON dump. */
function describeIntakeFailure(cause: unknown, t: (en: string, zh: string) => string): string {
  if (cause instanceof ServiceError && cause.code === "unauthorized")
    return t(
      "Not signed in or no permission. Sign in again and retry.",
      "未登入或無存取權限，請重新登入後再試。",
    );
  if (cause instanceof ServiceError && cause.code === "invalid_request")
    return t(
      "The service rejected this request as malformed. Check the fields and try again.",
      "服務拒絕了此請求（格式不符），請檢查欄位後再試。",
    );
  if (cause instanceof IntakeRequestError) {
    if (cause.status === 413)
      return t(
        "The service refused this file as too large (64MB limit). Nothing was saved.",
        "服務拒絕此檔案：太大（上限 64MB），未保存。",
      );
    if (cause.status === 429) return t("Please try again later.", "請稍後再試。");
    if (cause.status === 409)
      return t(
        "This operation key was already used with different content. Retry the action once, without reusing the old form state.",
        "此操作鍵已用於不同內容，請重新操作一次，不要沿用先前狀態。",
      );
    return t("The service refused this request.", "服務拒絕了此請求。");
  }
  if (cause instanceof OperationKeySourceError)
    return t(
      "This browser offers no cryptographic randomness, so a safe operation key cannot be minted.",
      "此瀏覽器無法提供加密隨機性，無法產生安全的操作鍵。",
    );
  return t(
    "The service did not give a usable answer, so it is unclear whether this was recorded. Refresh the list to check.",
    "服務未給出可用回覆，無法確認是否已完成；請重新整理清單確認。",
  );
}
