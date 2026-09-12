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
      <p className="muted">
        {t("Case", "案件")} {task.run.revision.case_id} · {t("revision", "修訂")}{" "}
        {task.run.revision.revision_id} · {t("task version", "任務版本")} {task.version}
      </p>

      <p style={{ fontSize: "1.05rem" }}>{taskQuestionText(task.reason_code, task.question, t)}</p>

      {/* A reviewer answering from the question alone is the failure this section exists to
          prevent, so evidence comes before the form, not after it. */}
      {subject ? (
        <ValueAuthority
          change={{
            schema_version: "service-v1",
            subject_id: subject.subject_id,
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

      <h3>{t("Findings this task answers", "本次回覆對應的檢核紀錄")}</h3>
      <ul>
        {task.finding_ids.map((id) => (
          <li key={id}>
            <code>{id}</code>
          </li>
        ))}
      </ul>

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
