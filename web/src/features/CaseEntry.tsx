import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { ReviewClient, ReviewSessionView, CaseContextView, JobStatusView } from "@/api/client";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";
import { statusText } from "./workbench-state";

export function CaseEntry({
  client,
  session,
  recent,
}: {
  client: ReviewClient;
  session: ReviewSessionView;
  recent: Map<string, string>;
}) {
  const t = useText();
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [rows, setRows] = useState<
    Array<{ id: string; context: CaseContextView; status: JobStatusView }>
  >([]);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const ids = JSON.stringify([
    ...new Set([...session.configured_jobs.map((job) => job.job_id), ...recent.keys()]),
  ]);
  const [reload, setReload] = useState(0);
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
  return (
    <article>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t("REVIEW WORKSPACE", "案件審查工作台")}</span>
          <h1>{t("Your configured cases", "接續你的審查工作")}</h1>
          <p className="lead">
            {t(
              "Open an authorized case and find the next decision.",
              "開啟已授權案件，找到現在需要你處理的事項。",
            )}
          </p>
        </div>
      </div>
      <div className="notice" data-tone="neutral">
        <Icon name="file" />
        {t(
          "Configured and recently opened jobs only. This is not a complete case registry; access is checked again when opened.",
          "這裡僅列已配置／本次工作階段最近開啟的案件工作，不是完整案件庫；開啟時會再次驗權。",
        )}
      </div>
      <section className="panel" aria-label={t("Configured cases", "已配置案件")}>
        <div className="panel-heading">
          <h2>{t("Configured / recently opened", "已配置／最近開啟")}</h2>
          <button onClick={() => setReload((n) => n + 1)}>{t("Refresh list", "更新清單")}</button>
        </div>
        <div className="toolbar">
          <label className="search-box">
            <Icon name="search" />
            <span className="sr-only">{t("Search configured cases", "搜尋已配置案件")}</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("District, use or case identifier", "搜尋行政區、用途或案件識別碼")}
            />
          </label>
          <div className="filters" aria-label={t("Filter configured cases", "篩選已配置案件")}>
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
            <h3>{t("No visible entry in this view", "目前檢視沒有可列出的案件")}</h3>
            <p>
              {t(
                "Change the filter or use a job reference provided by the local operator.",
                "可調整篩選條件，或使用本機管理者提供的案件工作參照。",
              )}
            </p>
          </div>
        ) : (
          <div className="case-list">
            {shown.map((row, index) => (
              <div className="case-row" key={row.id}>
                <span className="round-icon">
                  <Icon name="file" />
                </span>
                <div className="case-name">
                  <h3>
                    {row.context.identity?.district ||
                      t("District not available", "行政區尚未提供")}{" "}
                    ·{" "}
                    {row.context.identity?.land_use_category ||
                      t("Use not available", "用途尚未提供")}
                  </h3>
                  <span className="muted">
                    {t("Configured entry", "配置項目")} {index + 1} ·{" "}
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
      </section>
      <div className="two-columns">
        <section className="panel">
          <h2>
            <Icon name="file" />
            {t("Open a supplied reference", "開啟指定案件工作")}
          </h2>
          <p className="muted">
            {t(
              "For a configured job outside this limited view. The API checks access before returning case data.",
              "若案件不在這份有限清單，可使用管理者提供的工作參照；服務會先檢查權限。",
            )}
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (value.trim()) void navigate(`/jobs/${encodeURIComponent(value.trim())}/progress`);
            }}
          >
            <label htmlFor="job">{t("Job identifier", "案件工作識別碼")}</label>
            <div className="input-action">
              <input
                id="job"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                autoComplete="off"
              />
              <button data-variant="primary" disabled={!value.trim()}>
                {t("Open", "開啟")}
              </button>
            </div>
          </form>
        </section>
        <section className="panel soft">
          <h2>
            <Icon name="shield" />
            {t("What is available now", "本階段可以做什麼")}
          </h2>
          <p>
            {t(
              "Read progress and evidence, then confirm or correct only the exact actions offered by the service.",
              "查看進度與證據，再依服務允許的動作，確認觀察或提出更正。",
            )}
          </p>
          <p className="muted">
            {t(
              "This interface does not offer general upload, case creation, account registration or rule publishing. Ask the local operator about configured imports.",
              "此介面未提供一般上傳、新建案件、正式帳號註冊或規則發布。受控匯入請洽本機管理者。",
            )}
          </p>
        </section>
      </div>
    </article>
  );
}
