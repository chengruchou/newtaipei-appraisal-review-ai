import { Link } from "react-router-dom";
import type { CaseContextView, TaskListView } from "@/api/client";
import { Icon } from "@/ui/Icon";
import { useText } from "@/ui/Language";
import { subjectRoleText, type SubjectRole } from "./workbench-state";

export interface SubjectRow {
  id: string;
  role: SubjectRole;
  /** null means the service supplied no observations for this subject at all. */
  observations: number | null;
  openTasks: number;
  answeredTasks: number;
  openTaskId: string | null;
  pages: string[];
}

const sameRevision = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/**
 * Forms 4 and 5 fix their horizontal axis as one target plus its comparables, so this
 * roster mirrors the official layout. The identifiers come from the service's own
 * comparison contexts; document page order never decides a role.
 */
export function subjectRows(context: CaseContextView, tasks: TaskListView): SubjectRow[] {
  const contexts = context.rule_bundle?.contexts ?? [];
  const targets = [...new Set(contexts.map((item) => item.target_id))];
  const comparables = [...new Set(contexts.map((item) => item.comparable_id))].filter(
    (id) => !targets.includes(id),
  );
  const roster: { id: string; role: SubjectRole }[] = [
    ...targets.map((id) => ({ id, role: "target" as const })),
    ...comparables.map((id) => ({ id, role: "comparable" as const })),
  ];
  return roster.map(({ id, role }) => {
    const observations = context.observations.filter((item) =>
      role === "target"
        ? item.side.side === "target" && item.side.context.target_id === id
        : item.side.side === "comparable" && item.side.context.comparable_id === id,
    );
    const owned = tasks.tasks.filter((view) => {
      const side = view.task.side;
      if (!side) return false;
      return side.side === role
        ? role === "target"
          ? side.context.target_id === id
          : side.context.comparable_id === id
        : false;
    });
    const open = owned.filter(
      (view) =>
        view.task.state === "open" && sameRevision(view.task.run.revision, context.revision),
    );
    return {
      id,
      role,
      observations: observations.length ? observations.length : null,
      openTasks: open.length,
      answeredTasks: owned.filter((view) => view.task.state === "answered").length,
      openTaskId: open[0]?.task.task_id ?? null,
      pages: [
        ...new Set(
          observations.flatMap((item) =>
            item.observation.evidence.map(
              (citation) => `${citation.document_id} · ${citation.page}`,
            ),
          ),
        ),
      ].sort(),
    };
  });
}

export function SubjectRoster({
  context,
  tasks,
  jobId,
}: {
  context: CaseContextView;
  tasks: TaskListView;
  jobId: string;
}) {
  const t = useText();
  const rows = subjectRows(context, tasks);
  return (
    <section className="panel subject-roster" aria-label={t("Subject roster", "標的清單")}>
      <h2>
        <Icon name="file" />
        {t("Target and comparables", "比準地與比較標的")}
      </h2>
      <p className="small muted">
        {t(
          "Roles are read from the service's comparison contexts, not from document page order. A subject with no supplied observation is reported as not supplied, never as zero.",
          "角色來自服務提供的比較情境，不依文件頁序推定。未提供觀察值的標的顯示為「尚未提供」，不會顯示為 0。",
        )}
      </p>
      {rows.length ? (
        <ul className="plain">
          {rows.map((row) => (
            <li className="subject-row" key={`${row.role}:${row.id}`}>
              <div>
                <span
                  className="status-pill"
                  data-status={row.openTasks ? "needs_review" : "received"}
                >
                  {subjectRoleText(row.role, t)}
                </span>
                <strong>{row.id}</strong>
                <p className="muted">
                  {t("Supplied observations", "已提供觀察值")}：
                  {row.observations ?? t("Not supplied", "尚未提供")} ·{" "}
                  {t("Outstanding tasks", "待處理人工作業")}：{row.openTasks} ·{" "}
                  {t("Recorded responses", "已記錄回覆")}：{row.answeredTasks}
                </p>
                <p className="small muted">
                  {t("Cited source pages", "引用來源頁碼")}：
                  {row.pages.join("; ") || t("None supplied", "服務未提供")}
                </p>
              </div>
              {row.openTaskId ? (
                <Link className="button" to={`/jobs/${jobId}/tasks/${row.openTaskId}`}>
                  {t("Open the current task", "開啟目前任務")}
                  <Icon name="arrow" />
                </Link>
              ) : (
                <span className="small muted">
                  {t("No open task for this subject", "此標的目前沒有待處理任務")}
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty-state">
          {t(
            "The service published no comparison contexts for this revision, so no subject roster can be shown. Subjects are not invented here.",
            "此修訂未提供比較情境，因此無法列出標的清單；此處不會自行補造標的。",
          )}
        </p>
      )}
      <p className="small muted">
        {t(
          "A recorded response may be a confirmation, a correction or a refusal. It is not by itself a confirmed value.",
          "已記錄的回覆可能是確認、更正或拒絕，本身不代表數值已確認。",
        )}
      </p>
    </section>
  );
}
