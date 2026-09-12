import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, useParams, useSearchParams } from "react-router-dom";
import type {
  ArtifactManifest,
  CaseContextView,
  JobStatusView,
  PausedReviewView,
  ReviewClient,
  RevisionListView,
  ServiceResult,
  SourceCitation,
  TaskListView,
} from "@/api/client";
import { ServiceError } from "@/api/problems";
import { EvidenceList } from "@/ui/Evidence";
import { renderValue } from "@/ui/Authority";
import { useText } from "@/ui/Language";
import { Icon } from "@/ui/Icon";
import { TaskPage } from "./TaskPage";
import { CaseContext } from "./CaseContext";
import { BlockerList } from "./BlockerList";
import { ConditionEntry } from "./ConditionEntry";
import { OfficialForms } from "./OfficialForms";
import { SubjectRoster } from "./SubjectRoster";
import {
  categoryLabel,
  documentPurposeText,
  findingCategory,
  findingKindText,
  statusText,
  taskQuestionText,
  type FindingCategory,
} from "./workbench-state";

interface JobData {
  job: JobStatusView;
  context: CaseContextView;
  tasks: TaskListView;
  revisions: RevisionListView;
  result: ServiceResult | null;
  assessment: PausedReviewView | null;
  assessmentError: boolean;
}
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function Problem({ error, retry }: { error: unknown; retry: () => void }) {
  const t = useText();
  const code = error instanceof ServiceError ? error.code : "unknown";
  const words: Record<string, [string, string]> = {
    unauthorized: [
      "Access is no longer permitted. Check the local session and case permissions.",
      "目前無權存取。請確認本機工作階段與案件權限。",
    ],
    not_found: [
      "This case is not available to this session. Return to the configured cases.",
      "此工作階段無法開啟這個案件，請返回已配置案件。",
    ],
    version_conflict: [
      "The current revision changed while loading. Read the current state again.",
      "載入期間版本已更新，請重新讀取目前狀態。",
    ],
    capability_unavailable: [
      "This capability is unavailable in the configured local service.",
      "本機服務目前尚未提供此能力，請確認服務配置。",
    ],
    execution_failed: [
      "The service could not complete this read. No review success is implied.",
      "服務未能完成讀取，不能視為審查成功。",
    ],
    invalid_request: [
      "The supplied reference is invalid. Use a reference supplied by the operator.",
      "工作參照格式不符，請使用管理者提供的參照。",
    ],
    unknown: [
      "The current state could not be verified. Check the service before continuing.",
      "目前無法查證狀態，請確認本機服務後再繼續。",
    ],
  };
  return (
    <section role="alert" className="notice" data-tone="warn">
      <h2>{t("Current data unavailable", "目前資料無法載入")}</h2>
      <p>{t(...(words[code] ?? words.unknown!))}</p>
      <button onClick={retry}>{t("Read current state", "重新查詢目前狀態")}</button>
    </section>
  );
}

export function WorkbenchJob({
  client,
  recent,
}: {
  client: ReviewClient;
  recent: Map<string, string>;
}) {
  const { jobId = "", "*": route = "progress" } = useParams();
  const page = route.split("/")[0] || "progress";
  const taskId = route.startsWith("tasks/") ? route.slice("tasks/".length) : null;
  const t = useText();
  const [data, setData] = useState<JobData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [refresh, setRefresh] = useState(0);
  const reload = useCallback(() => setRefresh((n) => n + 1), []);
  useEffect(() => {
    let active = true;
    let loading = false;
    async function read() {
      if (loading || !active) return;
      loading = true;
      try {
        const job = await client.readJob(jobId);
        const [context, tasks, revisions] = await Promise.all([
          client.readCaseContext(jobId),
          client.listJobTasks(jobId),
          client.listJobRevisions(jobId),
        ]);
        if (
          !equal(context.revision, job.current_run?.revision) ||
          tasks.job.job_id !== jobId ||
          revisions.job.job_id !== jobId
        )
          throw new ServiceError("version_conflict", 409);
        let result: ServiceResult | null = null;
        let assessment: PausedReviewView | null = null;
        let assessmentError = false;
        if (job.job_status === "waiting_for_human") {
          try {
            assessment = await client.readAssessment(jobId);
          } catch (cause) {
            if (
              cause instanceof ServiceError &&
              ["unauthorized", "not_found", "version_conflict"].includes(cause.code)
            )
              throw cause;
            assessmentError = true;
          }
        } else if (
          job.result_version > 0 &&
          ["succeeded", "failed", "cancelled"].includes(job.job_status)
        )
          result = await client.readJobResult(jobId);
        if (
          (result &&
            (!equal(result.run.revision, context.revision) ||
              result.result_version !== job.result_version)) ||
          (assessment &&
            (!equal(assessment.run.revision, context.revision) ||
              assessment.run.run_id !== job.current_run?.run_id))
        )
          throw new ServiceError("version_conflict", 409);
        const latest = await client.readJob(jobId);
        if (!equal(latest, job)) throw new ServiceError("version_conflict", 409);
        if (!active) return;
        const next = { job, context, tasks, revisions, result, assessment, assessmentError };
        setData((previous) => {
          if (!previous) return next;
          return Object.fromEntries(
            Object.entries(next).map(([key, value]) => [
              key,
              equal(value, previous[key as keyof JobData]) ? previous[key as keyof JobData] : value,
            ]),
          ) as unknown as JobData;
        });
        recent.set(jobId, job.job.case_id);
        setError(null);
        setUpdated(new Date());
      } catch (cause) {
        if (active) {
          setData(null);
          setError(cause);
          if (cause instanceof ServiceError && ["unauthorized", "not_found"].includes(cause.code))
            recent.delete(jobId);
        }
      } finally {
        loading = false;
      }
    }
    void read();
    const timer = setInterval(() => {
      if (document.visibilityState !== "hidden") void read();
    }, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [client, jobId, recent, refresh]);
  const titles: Record<string, [string, string]> = {
    progress: ["Review progress", "審查進度"],
    results: ["Review overview", "審查摘要"],
    evidence: ["Source and rule comparison", "來源與規則比對"],
    tasks: ["Human review", "人工作業"],
    forms: ["Official forms", "官方表格"],
  };
  return (
    <article>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t("CURRENT REVIEW", "目前審查輪次")}</span>
          <h1>{t(...(titles[page] ?? titles.progress!))}</h1>
          {data ? (
            <p className="lead">
              {data.context.identity?.district || t("District unavailable", "行政區尚未提供")} ·{" "}
              {data.context.identity?.zone || t("Zone unavailable", "區段尚未提供")} ·{" "}
              {data.context.identity?.land_use_category || t("Use unavailable", "用途尚未提供")}
            </p>
          ) : null}
        </div>
        <button onClick={reload}>
          <Icon name="clock" />
          {t("Refresh", "更新狀態")}
        </button>
      </div>
      <nav className="case-tabs" aria-label={t("Case views", "案件視圖")}>
        {Object.entries(titles).map(([key, words]) => (
          <NavLink key={key} to={`/jobs/${jobId}/${key}`}>
            {t(...words)}
          </NavLink>
        ))}
      </nav>
      {error ? (
        <Problem error={error} retry={reload} />
      ) : !data ? (
        <div className="loading-state" role="status">
          <span className="loading-dot" />
          {t("Loading authorized current state…", "正在讀取已授權的目前狀態…")}
        </div>
      ) : (
        <>
          <CaseContext context={data.context} />
          {page === "results" ? (
            <Results
              data={data}
              jobId={jobId}
              client={client}
              onUnavailable={() => {
                setData(null);
                reload();
              }}
            />
          ) : page === "evidence" ? (
            <EvidenceComparison data={data} jobId={jobId} client={client} />
          ) : page === "forms" ? (
            <OfficialForms context={data.context} />
          ) : page === "tasks" ? (
            taskId ? (
              <TaskPage
                key={`${taskId}:${data.context.revision.revision_id}:${data.context.revision.material_digest}`}
                taskId={taskId}
                client={client}
                onCommitted={reload}
              />
            ) : (
              <TaskList data={data} jobId={jobId} />
            )
          ) : (
            <Progress data={data} jobId={jobId} />
          )}
          <p className="update-line">
            {t("Last confirmed local read", "最近一次本機查詢確認")}：
            {updated?.toLocaleTimeString("zh-TW", { timeZone: "Asia/Taipei", hour12: false }) ??
              "—"}{" "}
            · Asia/Taipei · {t("Checks every 5 seconds while visible", "頁面可見時每 5 秒查詢")}
          </p>
        </>
      )}
    </article>
  );
}

function Progress({ data, jobId }: { data: JobData; jobId: string }) {
  const t = useText();
  const { job } = data;
  const states = ["queued", "running", "waiting_for_human", "succeeded"];
  return (
    <>
      <section
        className="progress-track"
        aria-label={t(
          "Current execution state, not an event timeline",
          "目前執行狀態，並非歷史事件時間軸",
        )}
      >
        {states.map((state) => (
          <div
            key={state}
            data-current={
              job.job_status === state || (state === "queued" && job.job_status === "dispatched")
            }
          >
            <span className="round-icon">
              <Icon
                name={
                  state === "waiting_for_human"
                    ? "person"
                    : state === "succeeded"
                      ? "check"
                      : "clock"
                }
              />
            </span>
            <strong>{statusText(state, t)}</strong>
            <span>{job.job_status === state ? t("Current state", "目前狀態") : ""}</span>
          </div>
        ))}
      </section>
      <div className="two-columns">
        <section className="panel">
          <h2>{t("Current work", "目前工作狀態")}</h2>
          <p className="status-large">{statusText(job.job_status, t)}</p>
          <p>
            {job.job_status === "waiting_for_human"
              ? t(
                  "The run is waiting for explicit human responses. It is not an infrastructure failure.",
                  "本輪正在等待明確的人工作業，這不是基礎服務失敗。",
                )
              : job.job_status === "succeeded"
                ? t(
                    "Execution succeeded. Business verification and artifact publication are separate checks.",
                    "執行已成功；業務檢核與成果發布仍是不同狀態。",
                  )
                : job.job_status === "retryable_failed"
                  ? t(
                      "A retry is eligible, but no retry is claimed to be running. Operator recovery is separate.",
                      "此工作可重試，但尚未宣稱正在重試，需依管理者復原流程處理。",
                    )
                  : t(
                      "This state comes from the durable job API. No completion percentage or ETA is estimated.",
                      "狀態來自耐久工作 API，不估算完成百分比或剩餘時間。",
                    )}
          </p>
          {job.problem ? (
            <p className="notice" data-tone="danger">
              {t("Service reported", "服務回報")}：{job.problem.code}
            </p>
          ) : null}
          {job.cancel_requested ? (
            <p className="notice" data-tone="warn">
              {t(
                "Cancellation requested; not yet confirmed stopped.",
                "已要求取消，尚不代表已停止。",
              )}
            </p>
          ) : null}
          <dl className="kv">
            <dt>{t("Execution attempts", "已記錄執行次數")}</dt>
            <dd>{job.attempt_count}</dd>
            <dt>{t("Current result version", "已提交結果版本")}</dt>
            <dd>{job.result_version}</dd>
          </dl>
        </section>
        <section className="panel">
          <h2>
            <Icon name="arrow" />
            {t("Next step", "接下來可以做什麼")}
          </h2>
          <p>
            {t(
              "Read the current findings and sources before responding. A submitted response schedules its own continuation.",
              "先看本輪發現與來源，再處理服務允許的回覆；提交成功後，服務會依回覆安排後續處理。",
            )}
          </p>
          <Link className="button primary wide" to={`/jobs/${jobId}/results`}>
            {t("View review overview", "查看審查摘要")}
            <Icon name="arrow" />
          </Link>
          <Link className="button wide" to={`/jobs/${jobId}/tasks`}>
            {t("View human tasks", "查看人工作業")}
          </Link>
        </section>
      </div>
      <SubjectRoster context={data.context} tasks={data.tasks} jobId={jobId} />
      <ConditionEntry context={data.context} tasks={data.tasks} jobId={jobId} />
      <section className="panel">
        <h2>{t("Pinned documents", "本輪固定文件")}</h2>
        <ul className="document-list">
          {data.context.documents?.map((document) => (
            <li key={document.document_id}>
              <Icon name="file" />
              <div>
                <strong>{document.document_id}</strong>
                <span className="muted">
                  {documentPurposeText(document.purpose, t)} · v{document.version}
                </span>
              </div>
            </li>
          ))}
        </ul>
        <p className="small muted">
          {t(
            "Document identities come from the revision API; page counts are not inferred.",
            "文件身分來自修訂 API，不推測頁數或上傳狀態。",
          )}
        </p>
      </section>
    </>
  );
}

function TaskList({ data, jobId }: { data: JobData; jobId: string }) {
  const t = useText();
  const open = data.tasks.tasks.filter(
    (view) => view.task.state === "open" && equal(view.task.run.revision, data.context.revision),
  );
  const closed = data.tasks.tasks.filter((view) => !open.includes(view));
  return (
    <>
      <section className="panel">
        <h2>{t("Open tasks", "目前待處理")}</h2>
        <p className="muted">
          {t(
            "Confirmations, corrections and approvals are separate. Raw confidence remains unchanged.",
            "確認、更正與核准分開；原始擷取信心值保持不變。",
          )}
        </p>
        {open.length ? (
          <ul className="plain">
            {open.map((view) => (
              <li className="task-row" key={view.task.task_id}>
                <span className="round-icon">
                  <Icon name="person" />
                </span>
                <div>
                  <strong>{taskQuestionText(view.task.reason_code, view.task.question, t)}</strong>
                  <p className="muted">
                    {view.task.side?.factor_id} ·{" "}
                    {view.task.side?.side === "target"
                      ? t("Target side", "基準側")
                      : view.task.side?.side === "comparable"
                        ? t("Comparable side", "比較側")
                        : t("See exact subject", "查看精確主體")}{" "}
                    · {t("Task version", "任務版本")} {view.task.version}
                  </p>
                </div>
                <Link className="button primary" to={`/jobs/${jobId}/tasks/${view.task.task_id}`}>
                  {t("Review and respond", "核對並回覆")}
                  <Icon name="arrow" />
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-state">
            {t("Nothing is waiting for you on this job.", "本輪目前沒有可回覆的人工作業。")}
          </p>
        )}
      </section>
      {closed.length ? (
        <details className="panel">
          <summary>
            {t("Handled or superseded tasks", "已處理或已取代的任務")} ({closed.length})
          </summary>
          <ul>
            {closed.map((view) => (
              <li key={view.task.task_id}>
                <Link to={`/jobs/${jobId}/tasks/${view.task.task_id}`}>{view.task.question}</Link> ·{" "}
                {statusText(view.task.state, t)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <details className="panel">
        <summary>{t("Revision history", "修訂歷程")}</summary>
        <p className="muted">
          {t(
            "Only recorded revisions are listed; no timestamps or approval events are invented.",
            "僅列服務記錄的修訂，不編造時間或核准事件。",
          )}
        </p>
        <ol>
          {data.revisions.revisions.map((revision) => (
            <li key={revision.reference.revision_id}>
              <code>{revision.reference.revision_id}</code>
              {revision.changes.map((change) => (
                <p key={change.subject_id}>
                  {change.subject_id}：{renderValue(change.original, t)} →{" "}
                  {renderValue(change.corrected, t)} · {t("Raw confidence", "原始信心值")}{" "}
                  {change.original.confidence ?? t("Unknown", "未知")}
                </p>
              ))}
            </li>
          ))}
        </ol>
      </details>
    </>
  );
}

function FindingsUnavailable({ data }: { data: Pick<JobData, "assessmentError"> }) {
  const t = useText();
  return (
    <div className="panel empty-state">
      <Icon name="clock" />
      <h2>{t("No current assessment available", "尚無可讀取的本輪審查摘要")}</h2>
      <p>
        {data.assessmentError
          ? t(
              "The paused assessment could not be loaded. Human tasks remain available; this is not zero findings.",
              "暫停中的摘要目前無法載入，仍可查看人工作業；這不代表零問題。",
            )
          : t(
              "The service has not supplied current findings. Continue from the actual job and task state.",
              "服務尚未提供本輪發現，請依實際工作與任務狀態接續。",
            )}
      </p>
    </div>
  );
}

function Results({
  data,
  jobId,
  client,
  onUnavailable,
}: {
  data: JobData;
  jobId: string;
  client: ReviewClient;
  onUnavailable: () => void;
}) {
  const t = useText();
  const [filter, setFilter] = useState<FindingCategory | "all">("all");
  const [search, setSearch] = useState("");
  const source = data.result ?? data.assessment;
  if (!source) return <FindingsUnavailable data={data} />;
  const findings = source.findings;
  const categories: FindingCategory[] = ["matched", "content", "rules", "evidence", "uncovered"];
  const shown = findings
    .map((finding, index) => ({ finding, index }))
    .filter(
      ({ finding }) =>
        (filter === "all" || findingCategory(finding) === filter) &&
        JSON.stringify([finding.factor_id, finding.kind, finding.trace])
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase()),
    );
  return (
    <>
      <div className="notice" data-tone={data.assessment ? "warn" : "neutral"}>
        {data.assessment
          ? t(
              "Paused assessment: these are the stored findings for the human handoff, not a committed result or a ready report.",
              "等待人工中的審查摘要：這是本次交接的已儲存發現，不是正式已提交結果，也不代表報告就緒。",
            )
          : t(
              "Current committed result. Execution, business checks and downloadable output are distinct.",
              "目前已提交結果。執行、業務檢核與可下載成果分開判定。",
            )}
      </div>
      <div className="metrics">
        {categories.map((category) => (
          <button
            className="metric"
            key={category}
            aria-pressed={filter === category}
            onClick={() => setFilter(filter === category ? "all" : category)}
          >
            <span className="round-icon">
              <Icon
                name={category === "matched" ? "check" : category === "rules" ? "book" : "file"}
              />
            </span>
            <span>
              <span className="muted">{categoryLabel(category, t)}</span>
              <strong>
                {findings.filter((finding) => findingCategory(finding) === category).length}
              </strong>
            </span>
          </button>
        ))}
      </div>
      <p className="small muted">
        {t(
          "Counts are finding records, including source, rule and value checks. They are not factor counts or whole-case coverage percentages.",
          "計數單位是檢核紀錄，包含來源、規則與數值檢核；不是因素數，也不是全案覆蓋率。",
        )}
      </p>
      <BlockerList
        input={{
          context: data.context,
          tasks: data.tasks,
          assessment: data.assessment,
          result: data.result,
          assessmentError: data.assessmentError,
        }}
        jobId={jobId}
      />
      <section className="panel">
        <div className="toolbar">
          <label className="search-box">
            <Icon name="search" />
            <span className="sr-only">{t("Search findings", "搜尋檢核紀錄")}</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("Search factor or reason", "搜尋因素或原因")}
            />
          </label>
          <button onClick={() => setFilter("all")} aria-pressed={filter === "all"}>
            {t("All findings", "全部紀錄")}
          </button>
        </div>
        <div
          className="table-scroll"
          role="region"
          tabIndex={0}
          aria-label={t(
            "Findings table; scroll horizontally for all columns",
            "檢核紀錄表，可橫向捲動查看所有欄位",
          )}
        >
          <table>
            <thead>
              <tr>
                <th>{t("Item", "檢核項目")}</th>
                <th>{t("Outcome", "結果")}</th>
                <th>{t("Observed / expected", "填報／預期")}</th>
                <th>{t("Source", "來源")}</th>
                <th>{t("Action", "操作")}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map(({ finding, index }) => (
                <tr key={`${finding.id}:${index}`}>
                  <td>
                    <strong>{findingKindText(finding.kind, t)}</strong>
                    <small className="muted">{finding.factor_id ?? finding.kind}</small>
                  </td>
                  <td>
                    <span className="status-pill" data-status={finding.status}>
                      {categoryLabel(findingCategory(finding), t)}
                    </span>
                  </td>
                  <td>
                    {shownValue(finding.observed, t)} / {shownValue(finding.expected, t)}
                  </td>
                  <td>
                    {(finding.evidence ?? [])
                      .map(
                        (citation) =>
                          `${citation.document_id} · ${t("p.", "第")} ${citation.page} ${t("", "頁")}`,
                      )
                      .join("; ") || t("Not provided", "未提供")}
                  </td>
                  <td>
                    <Link className="button small" to={`/jobs/${jobId}/evidence?finding=${index}`}>
                      {t("Compare", "查看依據")}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!shown.length ? (
          <p>{t("No records match this filter.", "此篩選條件下沒有紀錄。")}</p>
        ) : null}
      </section>
      {source.verification ? (
        <section className="panel">
          <h2>{t("Independent verification", "獨立驗證診斷")}</h2>
          <p>{statusText(source.verification.status, t)}</p>
          {[...source.verification.critical_errors, ...source.verification.warnings].map(
            (diagnostic, index) => (
              <p className="notice" data-tone="warn" key={index}>
                <strong>{diagnostic.code}</strong> ·{" "}
                {diagnostic.code === "verification_blocker"
                  ? t(diagnostic.message, "尚有檢核阻擋，請查看本輪發現與待處理的人工作業。")
                  : diagnostic.message}
              </p>
            ),
          )}
        </section>
      ) : null}
      {data.result ? (
        <PublishedOutput
          result={data.result}
          client={client}
          jobId={jobId}
          onUnavailable={onUnavailable}
        />
      ) : (
        <p className="notice" data-tone="warn">
          {t(
            "No published PDF is available for this result.",
            "尚無已發布 PDF；未完成覆蓋、確認與核准不得視為正式報告就緒。",
          )}
        </p>
      )}
      <Link className="button primary" to={`/jobs/${jobId}/tasks`}>
        {t("Continue to human tasks", "接續人工作業")}
        <Icon name="arrow" />
      </Link>
    </>
  );
}

function shownValue(value: string | null | undefined, t: (a: string, b: string) => string) {
  return value == null || value === "None" ? t("Unavailable", "尚無可採用值") : value;
}

export function EvidenceComparison({
  data,
  jobId,
  client,
}: {
  data: Pick<JobData, "context" | "result" | "assessment" | "assessmentError">;
  jobId: string;
  client: ReviewClient;
}) {
  const t = useText();
  const [params, setParams] = useSearchParams();
  const source = data.result ?? data.assessment;
  const loadSource = useCallback(
    (citation: SourceCitation) => client.readSource(citation),
    [client],
  );
  if (!source) return <FindingsUnavailable data={data} />;
  const explicitIndex = params.get("finding");
  const firstEvidence = source.findings.findIndex(
    (item) => item.context != null && !!item.factor_id?.trim() && !!item.evidence?.length,
  );
  const index = explicitIndex === null ? Math.max(0, firstEvidence) : Number(explicitIndex);
  const finding = Number.isInteger(index) && index >= 0 ? source.findings[index] : undefined;
  if (!finding)
    return (
      <div className="panel">
        <p>
          {t(
            "Select an available finding from the current overview.",
            "請從本輪摘要選取可用的檢核紀錄。",
          )}
        </p>
        <Link to={`/jobs/${jobId}/results`}>{t("Back to overview", "返回摘要")}</Link>
      </div>
    );
  const observations = data.context.observations.filter(
    (item) =>
      equal(item.side.context, finding.context) &&
      (!finding.factor_id || item.side.factor_id === finding.factor_id),
  );
  const scopedRules = data.context.rules.filter(
    (rule) => finding.context != null && equal(rule.reference.context, finding.context),
  );
  return (
    <>
      <div className="toolbar">
        <label>
          {t("Current finding", "目前檢核紀錄")}
          <select
            value={String(index)}
            onChange={(event) => {
              const next = new URLSearchParams(params);
              next.set("finding", event.target.value);
              setParams(next);
            }}
          >
            {source.findings.map((item, i) => (
              <option key={i} value={i}>
                {item.factor_id ?? item.kind} · {statusText(item.status, t)}
              </option>
            ))}
          </select>
        </label>
        <span className="status-pill" data-status={finding.status}>
          {categoryLabel(findingCategory(finding), t)}
        </span>
      </div>
      <p className="small muted">
        {t(
          "Local originals are read through the separately paired loopback source provider, with current case authorization. Originals do not fall back to the review API or the separate privacy-review service.",
          "原件由獨立配對的本機（loopback）來源服務提供，須同時通過配對與案件授權；不會改由審查 API 或另一套隱私檢查服務取得。",
        )}
      </p>
      <div className="two-columns evidence-columns">
        <section className="panel">
          <h2>
            <Icon name="file" />
            {t("Finding citations", "本筆檢核引用證據")}
          </h2>
          <EvidenceList
            key={finding.id}
            citations={finding.evidence ?? []}
            loadSource={loadSource}
            label={t("Finding citations", "本筆檢核引用證據")}
          />
          <h3>{t("Original observations and comparison sides", "原始觀察值與比較側")}</h3>
          {observations.length ? (
            observations.map((item, i) => (
              <div className="observation-row" key={i}>
                <span>
                  {item.side.side === "target" ? t("Target", "基準側") : t("Comparable", "比較側")}{" "}
                  · {item.side.factor_id}
                </span>
                <strong>{renderValue(item.observation, t)}</strong>
                <small>
                  {t("Raw confidence", "原始信心值")}：
                  {item.observation.confidence ?? t("Unknown", "未知")}
                </small>
              </div>
            ))
          ) : (
            <p>
              {t(
                "No exact observation metadata was supplied for this finding. Values and units are not inferred.",
                "此紀錄未提供精確觀察值脈絡，不推測數值或單位。",
              )}
            </p>
          )}
        </section>
        <section className="panel">
          <h2>
            <Icon name="book" />
            {t("Rule references for this comparison scope", "同情境規則組參考")}
          </h2>
          <p>
            {t("Finding-reported rule", "本筆紀錄提供的規則")}：
            {finding.rule_id ?? t("Not specified", "未指定")} /{" "}
            {finding.rule_version ?? t("Version not supplied", "版本未提供")}
          </p>
          <p className="small muted">
            {t(
              "These references share the finding's comparison context. A scoped rule set may cover several factors; this does not establish a direct rule match for this finding.",
              "以下參考資料與本筆檢核具有相同比較情境。規則組可能涵蓋多個因素，不代表已確認本筆檢核直接對應的規則。",
            )}
          </p>
          {scopedRules.map((rule, i) => (
            <section
              key={`${rule.reference.content_hash}:${i}`}
              aria-label={`${rule.reference.rule_set_id} ${rule.reference.version}`}
            >
              <h3>
                {rule.reference.rule_set_id} · {t("Version", "版本")} {rule.reference.version}
              </h3>
              <p className="small muted">
                {rule.reference.context.scope === "regional"
                  ? t("Regional", "區域因素")
                  : t("Individual", "個別因素")}{" "}
                · {rule.reference.context.target_id} × {rule.reference.context.comparable_id}
              </p>
              <EvidenceList
                citations={rule.evidence}
                loadSource={loadSource}
                label={`${t("Scoped rule references", "情境規則組引用")} ${rule.reference.rule_set_id}`}
              />
            </section>
          ))}
          {!scopedRules.length ? (
            <p>
              {finding.context
                ? t(
                    "No rule references with this exact comparison context were supplied.",
                    "尚未提供與此比較情境完全相符的規則組參考。",
                  )
                : t(
                    "This finding has no comparison context. No scoped rule association is inferred; its supplied citations remain available on the left.",
                    "此檢核未提供比較情境，因此不推定規則組關聯；服務提供的本筆引用仍保留於左側。",
                  )}
            </p>
          ) : null}
          <p className="muted">
            {t(
              "Target is the matrix row; comparable is the column. No rule value is invented here.",
              "矩陣固定以基準側為列、比較側為欄，不在此補造規則值。",
            )}
          </p>
        </section>
      </div>
      <section className="comparison-values">
        <div>
          <span>{t("Reported value", "原表填報")}</span>
          <strong>{shownValue(finding.observed, t)}</strong>
        </div>
        <div>
          <span>{t("Independent expected value", "獨立重算預期值")}</span>
          <strong>{shownValue(finding.expected, t)}</strong>
        </div>
        <div>
          <span>{t("Comparison outcome", "差異判斷")}</span>
          <strong>{categoryLabel(findingCategory(finding), t)}</strong>
        </div>
      </section>
      <section className="panel">
        <h2>{t("Reason and calculation trace", "原因與計算依據")}</h2>
        <p className="trace">{finding.trace}</p>
        {finding.context ? (
          <dl className="kv">
            <dt>{t("Scope", "比較情境")}</dt>
            <dd>{finding.context.scope}</dd>
            <dt>{t("Target", "基準側")}</dt>
            <dd>{finding.context.target_id}</dd>
            <dt>{t("Comparable", "比較側")}</dt>
            <dd>{finding.context.comparable_id}</dd>
          </dl>
        ) : null}
      </section>
      <div className="toolbar">
        <Link className="button" to={`/jobs/${jobId}/results`}>
          {t("Back to overview", "返回摘要")}
        </Link>
        <Link className="button primary" to={`/jobs/${jobId}/tasks`}>
          {t("View permitted responses", "查看允許的人工作業")}
          <Icon name="arrow" />
        </Link>
      </div>
    </>
  );
}

function PublishedOutput({
  result,
  client,
  jobId,
  onUnavailable,
}: {
  result: ServiceResult;
  client: ReviewClient;
  jobId: string;
  onUnavailable: () => void;
}) {
  const t = useText();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function download(artifact: ArtifactManifest) {
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      const bytes = await client.downloadArtifact(jobId, artifact);
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${artifact.artifact_id}.pdf`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(
        t(
          "Verified PDF downloaded. Its hash matches this result's artifact manifest.",
          "PDF 已重新授權並下載，內容雜湊與本輪成果紀錄相符。",
        ),
      );
    } catch {
      setMessage(
        t(
          "Download could not be authorized or verified. Read the current result again.",
          "下載未能通過授權或內容驗證，請重新讀取目前結果。",
        ),
      );
      onUnavailable();
    } finally {
      setBusy(false);
    }
  }
  const available =
    result.business_status === "completed" &&
    result.execution_status === "succeeded" &&
    result.artifact_status === "written" &&
    result.artifacts.length > 0 &&
    result.verification?.status === "verified" &&
    result.verification.critical_errors.length === 0;
  return (
    <section className="panel">
      <h2>{t("Result and downloads", "本輪成果與下載")}</h2>
      <p>
        {t("Execution", "執行")}：{statusText(result.execution_status, t)} ·{" "}
        {t("Business review", "業務檢核")}：{statusText(result.business_status ?? "unknown", t)} ·
        PDF：{statusText(result.artifact_status, t)}
      </p>
      {available ? (
        result.artifacts.map((artifact) => (
          <div key={artifact.artifact_id}>
            <div className="artifact-row">
              <span>
                <Icon name="file" />
                {artifact.page_count} {t("pages", "頁")} ·{" "}
                {t(
                  "Published local output; formal business acceptance remains separate",
                  "已發布本機輸出；正式業務驗收仍須另行確認",
                )}
              </span>
              <button
                disabled={busy}
                data-variant="primary"
                onClick={() => {
                  void download(artifact);
                }}
              >
                {t("Download verified PDF", "下載已驗證 PDF")}
              </button>
            </div>
            <div className="table-scroll">
              <table aria-label={t("Artifact comparison contexts", "成果比較情境")}>
                <thead>
                  <tr>
                    <th>{t("Scope", "比較情境")}</th>
                    <th>{t("Target", "基準側")}</th>
                    <th>{t("Comparable", "比較側")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(artifact.schema_version === "artifact-manifest-v2"
                    ? artifact.contexts
                    : [artifact.context]
                  ).map((context) => (
                    <tr key={JSON.stringify(context)}>
                      <td>{context.scope}</td>
                      <td>{context.target_id}</td>
                      <td>{context.comparable_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      ) : (
        <p className="notice" data-tone="warn">
          {t(
            "No published PDF is available for this result.",
            "尚無可授權下載的本輪 PDF，不代表正式報告已就緒。",
          )}
        </p>
      )}
      {message ? <p role="status">{message}</p> : null}
    </section>
  );
}
