import { useEffect, useState } from "react";

import type {
  ResponseReceipt,
  ReviewClient,
  TaskView,
  TaskSubjectView,
  SourceCitation,
} from "@/api/client";
import { ServiceError } from "@/api/problems";
import { ValueAuthority } from "@/ui/Authority";
import { EvidenceList } from "@/ui/Evidence";
import { useText } from "@/ui/Language";
import { serviceProblemText } from "@/ui/ServiceProblemText";
import { useCallback } from "react";

import { ResponseForm, subjectMatches } from "./ResponseForm";
import { factorLabel, looksOpaque, parseSubjectId, sideLabel, subjectLabel } from "./field-labels";
import { taskQuestionText } from "./workbench-state";

type Load =
  { name: "loading" } | { name: "ready"; view: TaskView } | { name: "failed"; error: ServiceError };

const KIND_WORDS: Record<string, string> = {
  fact_confirmation: "Confirm an observation",
  material_correction: "Correct a value",
  evidence_supply: "Supply cited evidence",
  rule_approval: "Approve rules",
  material_approval: "Approve material",
  publication_authorization: "Authorize publication",
};

export function TaskPage({
  taskId,
  client,
  onCommitted,
}: {
  taskId: string;
  client: ReviewClient;
  onCommitted?: (receipt: ResponseReceipt) => void;
}) {
  const t = useText();
  const loadSource = useCallback(
    (citation: SourceCitation) => client.readSource(citation),
    [client],
  );
  const [subject, setSubject] = useState<TaskSubjectView | null>(null);
  const [load, setLoad] = useState<Load>({ name: "loading" });

  const [generation, setGeneration] = useState(0);
  const reload = () => setGeneration((value) => value + 1);
  useEffect(() => {
    let cancelled = false;
    setLoad({ name: "loading" });
    setSubject(null);
    void client
      .readTask(taskId)
      .then((view) => {
        if (!cancelled) setLoad({ name: "ready", view });
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setLoad({
            name: "failed",
            error:
              error instanceof ServiceError ? error : new ServiceError("execution_failed", 500),
          });
      });
    return () => {
      cancelled = true;
    };
  }, [client, taskId, generation]);
  useEffect(() => {
    let cancelled = false;
    setSubject(null);
    if (load.name === "ready") {
      void client
        .readTaskSubject(taskId)
        .then((value) => {
          if (!cancelled && subjectMatches(load.view, value)) setSubject(value);
        })
        .catch(() => {
          /* Metadata errors block correction without hiding other actions. */
        });
    }
    return () => {
      cancelled = true;
    };
  }, [load, client, taskId]);

  if (load.name === "loading") {
    return (
      <p role="status" aria-live="polite">
        {t("Loading this task…", "正在讀取目前任務…")}
      </p>
    );
  }

  if (load.name === "failed") {
    return (
      <div className="notice" data-tone="danger" role="alert">
        <h2 style={{ marginTop: 0 }}>{serviceProblemText(load.error.code, t).title}</h2>
        <p>{serviceProblemText(load.error.code, t).guidance}</p>
        <button onClick={reload}>{t("Reload this task", "重新讀取此任務")}</button>
      </div>
    );
  }

  const { view } = load;
  const task = view.task;
  // Readable identity for the main copy; the raw identifiers stay verbatim in 技術紀錄.
  const parsedSubject = parseSubjectId(view.subject_id);
  const rawFactor = task.side?.factor_id ?? parsedSubject?.factorId ?? null;
  const factor = factorLabel(rawFactor, t);
  const sideValue = task.side?.side ?? parsedSubject?.side ?? null;
  const targetId = task.side?.context.target_id ?? parsedSubject?.targetId ?? null;
  const comparableId = task.side?.context.comparable_id ?? parsedSubject?.comparableId ?? null;
  const identityLine = [
    looksOpaque(task.run.revision.case_id)
      ? null
      : `${t("Case", "案件")} ${task.run.revision.case_id}`,
    targetId && comparableId ? `${t("Subjects", "標的")} ${targetId} × ${comparableId}` : null,
    factor
      ? `${t("Field to confirm", "待確認欄位")} ${factor}${
          sideValue ? `（${sideLabel(sideValue, t)}）` : ""
        }`
      : null,
  ].filter((piece): piece is string => piece !== null);
  return (
    <article>
      <h2>
        {t(
          KIND_WORDS[task.kind] ?? task.kind,
          (
            {
              fact_confirmation: "逐側確認觀察值",
              material_correction: "更正觀察值",
              evidence_supply: "補充引用證據",
              rule_approval: "規則核准作業",
              material_approval: "材料核准作業",
              publication_authorization: "發布授權作業",
            } as Record<string, string>
          )[task.kind] ?? task.kind,
        )}
      </h2>
      {identityLine.length ? <p className="muted">{identityLine.join(" · ")}</p> : null}

      <p style={{ fontSize: "1.05rem" }}>{taskQuestionText(task.reason_code, task.question, t)}</p>

      {/* A reviewer answering from the question alone is the failure this section exists to
          prevent, so evidence comes before the form, not after it. */}
      {subject ? (
        <ValueAuthority
          change={{
            schema_version: "service-v1",
            // Presentation only: the heading speaks the readable field name; the exact
            // canonical subject_id stays verbatim in 技術紀錄 and in every submission.
            subject_id: subjectLabel(subject.subject_id, t) ?? subject.subject_id,
            original: subject.observation,
            proposed: null,
            corrected: null,
            corrected_by: null,
          }}
        />
      ) : (
        <p className="notice" data-tone="warn">
          {t(
            "Authoritative observation metadata is unavailable or loading. Corrections remain blocked.",
            "精確觀察值尚在讀取或無法取得，更正功能維持禁止。",
          )}
        </p>
      )}
      <h3>{t("Evidence", "核對來源證據")}</h3>
      <EvidenceList citations={task.evidence} loadSource={loadSource} />

      <details>
        <summary>{t("Technical record", "技術紀錄")}</summary>
        <dl className="kv">
          <dt>{t("Case", "案件識別碼")}</dt>
          <dd>
            <code>{task.run.revision.case_id}</code>
          </dd>
          <dt>{t("Revision", "修訂識別碼")}</dt>
          <dd>
            <code>{task.run.revision.revision_id}</code>
          </dd>
          <dt>{t("Task version", "任務版本")}</dt>
          <dd>{task.version}</dd>
          {view.subject_id ? (
            <>
              <dt>subject_id</dt>
              <dd>
                <code>{view.subject_id}</code>
              </dd>
            </>
          ) : null}
          {rawFactor ? (
            <>
              <dt>factor_id</dt>
              <dd>
                <code>{rawFactor}</code>
              </dd>
            </>
          ) : null}
          <dt>{t("Findings this task answers", "本次回覆對應的檢核紀錄")}</dt>
          <dd>
            <ul>
              {task.finding_ids.map((id) => (
                <li key={id}>
                  <code>{id}</code>
                </li>
              ))}
            </ul>
          </dd>
        </dl>
      </details>

      <h3>{t("Respond", "回覆此任務")}</h3>
      <ResponseForm
        key={`${task.task_id}:${task.version}`}
        view={view}
        subject={subject}
        client={client}
        onReload={reload}
        onCommitted={(receipt) => onCommitted?.(receipt)}
      />
    </article>
  );
}
