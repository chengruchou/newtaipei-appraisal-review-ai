import { useCallback, useEffect, useState } from "react";

import type {
  ResponseReceipt,
  ReviewClient,
  TaskView,
  TaskSubjectView,
  SourceCitation,
} from "@/api/client";
import { EXPLANATIONS, ServiceError } from "@/api/problems";
import { ValueAuthority } from "@/ui/Authority";
import { EvidenceList } from "@/ui/Evidence";

import { ResponseForm, subjectMatches } from "./ResponseForm";

type Load =
  { name: "loading" } | { name: "ready"; view: TaskView } | { name: "failed"; error: ServiceError };

const KIND_WORDS: Record<string, string> = {
  fact_confirmation: "Confirm an observation",
  material_correction: "Correct a value",
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
  const loadSource = useCallback(
    (citation: SourceCitation) => client.readSource(citation),
    [client],
  );
  const [subject, setSubject] = useState<TaskSubjectView | null>(null);
  const [load, setLoad] = useState<Load>({ name: "loading" });

  const reload = useCallback(() => {
    let cancelled = false;
    setLoad({ name: "loading" });
    client
      .readTask(taskId)
      .then((view) => {
        if (!cancelled) {
          setLoad({ name: "ready", view });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoad({
            name: "failed",
            error:
              error instanceof ServiceError ? error : new ServiceError("execution_failed", 500),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client, taskId]);

  useEffect(() => reload(), [reload]);
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
        Loading this task…
      </p>
    );
  }

  if (load.name === "failed") {
    return (
      <div className="notice" data-tone="danger" role="alert">
        <h2 style={{ marginTop: 0 }}>{EXPLANATIONS[load.error.code].title}</h2>
        <p>{EXPLANATIONS[load.error.code].guidance}</p>
      </div>
    );
  }

  const { view } = load;
  const task = view.task;
  return (
    <article>
      <h1>{KIND_WORDS[task.kind] ?? task.kind}</h1>
      <p className="muted">
        Case {task.run.revision.case_id} · revision {task.run.revision.revision_id} · task version{" "}
        {task.version}
      </p>

      <p style={{ fontSize: "1.05rem" }}>{task.question}</p>

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
          Authoritative observation metadata is unavailable or loading. Corrections remain blocked.
        </p>
      )}
      <h2>Evidence</h2>
      <EvidenceList citations={task.evidence} loadSource={loadSource} />

      <h2>Findings this task answers</h2>
      <ul>
        {task.finding_ids.map((id) => (
          <li key={id}>
            <code>{id}</code>
          </li>
        ))}
      </ul>

      <h2>Respond</h2>
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
