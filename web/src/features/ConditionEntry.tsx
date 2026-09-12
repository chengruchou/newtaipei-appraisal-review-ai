import { Link } from "react-router-dom";
import type { CaseContextView, TaskListView } from "@/api/client";
import { Icon } from "@/ui/Icon";
import { useText } from "@/ui/Language";
import { conditionFieldText, conditionMethodText, type ConditionField } from "./workbench-state";

/**
 * The canonical correction task is the only place a condition may change. A subject
 * identifier is never minted here, and a candidate is never submitted as a condition.
 */
function correctionTask(tasks: TaskListView, field: ConditionField): string | null {
  const match = tasks.tasks.find(
    (view) =>
      view.task.state === "open" &&
      view.task.kind === "material_correction" &&
      (view.task.affected_subject_ids ?? []).some(
        (subject) => subject === field || subject.split(":").includes(field),
      ),
  );
  return match?.task.task_id ?? null;
}

export function ConditionEntry({
  context,
  tasks,
  jobId,
}: {
  context: CaseContextView;
  tasks: TaskListView;
  jobId: string;
}) {
  const t = useText();
  const identity = context.identity;
  const bundle = context.rule_bundle;
  const candidates = bundle?.condition_candidates ?? [];
  return (
    <section className="panel condition-entry" aria-label={t("Case conditions", "案件條件")}>
      <h2>
        <Icon name="book" />
        {t("Case conditions", "案件條件")}
      </h2>
      <dl className="kv">
        <dt>{t("Administrative district", "行政區")}</dt>
        <dd>{identity?.district || t("Not supplied", "尚未提供")}</dd>
        <dt>{t("Section", "區段")}</dt>
        <dd>{identity?.zone || t("Not supplied", "尚未提供")}</dd>
        <dt>{t("Land use", "用途")}</dt>
        <dd>{identity?.land_use_category || t("Not supplied", "尚未提供")}</dd>
        <dt>{t("Effective date", "查估基準日")}</dt>
        <dd>{identity?.effective_date || t("Not supplied", "尚未提供")}</dd>
        <dt>{t("Conditions version", "條件版本")}</dt>
        <dd>{identity?.version || t("Not supplied", "尚未提供")}</dd>
      </dl>
      {bundle ? (
        <p className="notice" data-tone={bundle.conditions_confirmed ? "ok" : "warn"} role="status">
          {bundle.conditions_confirmed
            ? t(
                "The service records these conditions as confirmed for this bundle. Execution and publication remain separately authorized.",
                "服務記錄本組來源的案件條件已確認；執行與發布仍須另行授權。",
              )
            : t(
                "Case conditions are not confirmed for this bundle. Everything below is proposed, not applied.",
                "本組來源的案件條件尚未確認；以下皆為候選解讀，尚未套用。",
              )}
        </p>
      ) : (
        <p className="notice" data-tone="warn" role="status">
          {t(
            "No rule bundle was supplied for this revision, so this district reads as unsupported in this deployment. No other district's rules are used instead.",
            "此修訂未提供規則組合，因此本部署視此行政區為未支援；不會改用其他行政區的規則。",
          )}
        </p>
      )}
      <h3>{t("Proposed condition candidates", "案件條件候選解讀")}</h3>
      {candidates.length ? (
        <ul className="plain">
          {candidates.map((candidate, index) => {
            const taskId = correctionTask(tasks, candidate.field);
            return (
              <li className="rule-row condition-row" key={`${candidate.field}:${index}`}>
                <strong>
                  {conditionFieldText(candidate.field, t)}：{candidate.value}
                </strong>
                <p>
                  <span className="status-pill" data-status="needs_review">
                    {conditionMethodText(candidate.method, t)}
                  </span>{" "}
                  {candidate.interpretation}
                </p>
                {candidate.method === "manual_proposed" ? (
                  <p className="small muted">
                    {t(
                      "Supplied by the operator as a human interpretation, not parsed from an official source. A reviewer can correct it; the frontend applies it to no calculation.",
                      "由管理者提供的人工解讀，並非從官方來源解析而得。審查者可更正；前端不會將其套用於任何計算。",
                    )}
                  </p>
                ) : null}
                {candidate.evidence.length ? (
                  candidate.evidence.map((citation, n) => (
                    <blockquote key={n}>
                      {citation.document_id} · {t("page", "頁碼")} {citation.page} ·{" "}
                      {citation.region_id}：{citation.excerpt}
                    </blockquote>
                  ))
                ) : (
                  <p className="small muted">
                    {t("No source excerpt was supplied.", "服務未提供原文依據。")}
                  </p>
                )}
                {taskId ? (
                  <Link className="button small" to={`/jobs/${jobId}/tasks/${taskId}`}>
                    {t("Open the correction task", "開啟更正任務")}
                    <Icon name="arrow" />
                  </Link>
                ) : (
                  <p className="small muted">
                    {t(
                      "The service offers no open correction task for this condition, so it cannot be changed here.",
                      "服務未提供此條件的待處理更正任務，因此無法在此變更。",
                    )}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="empty-state">
          {t(
            "No condition candidate was published for this revision. No condition is synthesised here.",
            "此修訂未提供任何條件候選；此處不會自行合成條件。",
          )}
        </p>
      )}
      <p className="small muted">
        {t(
          "A candidate is a proposed interpretation with its origin shown. It is never displayed as a confirmed condition.",
          "候選僅為標示來源的建議解讀，不會以已確認條件的形式呈現。",
        )}
      </p>
    </section>
  );
}
