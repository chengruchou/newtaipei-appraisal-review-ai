import { Link } from "react-router-dom";
import type {
  CaseContextView,
  PausedReviewView,
  ServiceResult,
  SourceCitation,
  TaskListView,
} from "@/api/client";
import { Icon } from "@/ui/Icon";
import { useText } from "@/ui/Language";
import {
  blockerActionText,
  blockerKindText,
  statusText,
  type BlockerKind,
} from "./workbench-state";

export interface Blocker {
  kind: BlockerKind;
  /** The field, comparison or document the row is about. */
  field: string;
  /** The raw service code or message, kept verbatim for the details disclosure. */
  code: string;
  citation: SourceCitation | null;
  taskId: string | null;
}

export interface BlockerGroup {
  kind: BlockerKind;
  items: Blocker[];
}

export interface BlockerInput {
  context: CaseContextView;
  tasks: TaskListView;
  assessment: PausedReviewView | null;
  result: ServiceResult | null;
  assessmentError: boolean;
}

function matchingTask(tasks: TaskListView, field: string): string | null {
  const open = tasks.tasks.find(
    (view) =>
      view.task.state === "open" &&
      ((view.task.finding_ids ?? []).includes(field) ||
        (view.task.affected_subject_ids ?? []).includes(field)),
  );
  return open?.task.task_id ?? null;
}

/**
 * Everything here is already published by the current contract. Nothing is inferred: a
 * row exists only because the service reported that exact item.
 */
export function collectBlockers({
  context,
  tasks,
  assessment,
  result,
}: BlockerInput): BlockerGroup[] {
  const blockers: Blocker[] = [];
  for (const field of assessment?.coverage.missing ?? [])
    blockers.push({
      kind: "coverage_missing",
      field,
      code: `coverage.missing:${field}`,
      citation: null,
      taskId: matchingTask(tasks, field),
    });
  for (const field of assessment?.coverage.unsupported ?? [])
    blockers.push({
      kind: "coverage_unsupported",
      field,
      code: `coverage.unsupported:${field}`,
      citation: null,
      taskId: null,
    });
  const verification = (result ?? assessment)?.verification;
  for (const diagnostic of verification?.critical_errors ?? [])
    blockers.push({
      kind: "verification_critical",
      field: diagnostic.code,
      code: diagnostic.message,
      citation: null,
      taskId: null,
    });
  for (const diagnostic of verification?.warnings ?? [])
    blockers.push({
      kind: "verification_warning",
      field: diagnostic.code,
      code: diagnostic.message,
      citation: null,
      taskId: null,
    });
  for (const selection of context.selections)
    if (selection.status !== "unique")
      blockers.push({
        kind: selection.status === "missing" ? "selection_missing" : "selection_ambiguous",
        field: `${selection.context.target_id} × ${selection.context.comparable_id}`,
        code: `selection.${selection.status}`,
        citation: null,
        taskId: null,
      });
  for (const rule of context.rules)
    if (rule.declared_status !== "approved")
      blockers.push({
        kind: "rule_unapproved",
        field: `${rule.reference.rule_set_id} · ${rule.reference.version}`,
        code: `declared_status:${rule.declared_status}`,
        citation: rule.evidence[0] ?? null,
        taskId: null,
      });
  for (const source of context.rule_bundle?.sources ?? [])
    for (const reason of source.unresolved ?? [])
      blockers.push({
        kind: "source_unresolved",
        field: source.document_id,
        code: reason,
        citation: source.evidence[0] ?? null,
        taskId: null,
      });
  const order: BlockerKind[] = [
    "verification_critical",
    "coverage_missing",
    "selection_missing",
    "selection_ambiguous",
    "rule_unapproved",
    "coverage_unsupported",
    "source_unresolved",
    "verification_warning",
  ];
  return order
    .map((kind) => ({ kind, items: blockers.filter((item) => item.kind === kind) }))
    .filter((group) => group.items.length > 0);
}

function citationText(citation: SourceCitation, t: (a: string, b: string) => string): string {
  return `${citation.document_id} v${citation.version} · ${t("p.", "第")} ${citation.page} ${t("", "頁")} · ${citation.region_id}`;
}

/**
 * Replaces the four-number coverage line. Totals do not say what is missing or what a
 * reviewer can do next; an unreadable assessment and an empty one are never the same row.
 */
export function BlockerList({ input, jobId }: { input: BlockerInput; jobId: string }) {
  const t = useText();
  const groups = collectBlockers(input);
  const { assessment, result, assessmentError } = input;
  const counted = groups.reduce((total, group) => total + group.items.length, 0);
  return (
    <section className="panel blocker-list" aria-label={t("Outstanding items", "未決項目")}>
      <div className="panel-heading">
        <h2>
          <Icon name="clock" />
          {t("Blockers and outstanding items", "阻擋與未決項目")}
        </h2>
        <span className="status-pill" data-status={counted ? "needs_review" : "received"}>
          {counted} {t("recorded", "筆已回報")}
        </span>
      </div>
      {assessmentError ? (
        <p className="notice" data-tone="danger" role="alert">
          {t(
            "The paused assessment could not be read, so this list is incomplete. This is not an empty finding set.",
            "暫停中的審查摘要無法讀取，因此本清單並不完整；這不代表沒有問題。",
          )}
        </p>
      ) : null}
      {assessment ? (
        <p className="small muted">
          {t("Independent required inventory", "獨立必要檢查清單")}：
          {assessment.coverage.required.length} · {t("Verified", "已核對")}{" "}
          {assessment.coverage.verified.length} ·{" "}
          {t(
            "The rows below say which items remain and what to do next.",
            "以下逐項說明尚缺哪些項目，以及下一步可以做什麼。",
          )}
        </p>
      ) : null}
      {groups.length ? (
        <ul className="plain">
          {groups.map((group) => (
            <li className="blocker-row" key={group.kind}>
              <div className="blocker-head">
                <strong>{blockerKindText(group.kind, t)}</strong>
                <span className="status-pill" data-status="needs_review">
                  {group.items.length}
                </span>
              </div>
              <p className="blocker-fields">
                {t("Affected", "影響對象")}：{group.items.map((item) => item.field).join("、")}
              </p>
              <p>{blockerActionText(group.kind, t)}</p>
              <p className="small muted">
                {t("Source citation", "來源引用")}：
                {group.items
                  .map((item) => item.citation)
                  .filter((citation): citation is SourceCitation => citation != null)
                  .map((citation) => citationText(citation, t))
                  .join("; ") || t("Not supplied by the service", "服務未提供")}
              </p>
              <div className="blocker-actions">
                {group.items
                  .filter((item) => item.taskId)
                  .map((item) => (
                    <Link
                      className="button small"
                      key={item.taskId}
                      to={`/jobs/${jobId}/tasks/${item.taskId!}`}
                    >
                      {t("Open task for", "開啟任務")} {item.field}
                    </Link>
                  ))}
                {group.items.every((item) => !item.taskId) ? (
                  <Link className="button small" to={`/jobs/${jobId}/evidence`}>
                    {t("Inspect evidence and rules", "檢視證據與規則")}
                  </Link>
                ) : null}
              </div>
              <details>
                <summary>{t("Raw service codes", "原始服務代碼")}</summary>
                <ul>
                  {group.items.map((item, index) => (
                    <li key={`${item.code}:${index}`}>
                      <code>{item.field}</code> · {item.code}
                      {item.taskId ? null : (
                        <span className="muted">
                          {" "}
                          · {t("no matching open task", "無對應待處理任務")}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          ))}
        </ul>
      ) : assessment || result ? (
        <p className="empty-state">
          {t(
            "This revision reports no outstanding item in the published fields. Coverage of unpublished checks is not claimed.",
            "本修訂在已發布欄位中未回報未決項目；未發布的檢核不在此宣稱範圍內。",
          )}
        </p>
      ) : (
        <p className="empty-state">
          {t(
            "No current assessment or committed result was supplied, so no outstanding item can be listed. This is not zero blockers.",
            "服務尚未提供本輪審查摘要或已提交結果，因此無法列出未決項目；這不等於零阻擋。",
          )}
        </p>
      )}
      {result?.business_status ? (
        <p className="small muted">
          {t("Business review state", "業務檢核狀態")}：{statusText(result.business_status, t)}
        </p>
      ) : null}
    </section>
  );
}
