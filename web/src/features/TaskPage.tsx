import { useCallback, useEffect, useState } from "react";

import type { ResponseReceipt, ReviewClient, TaskView } from "@/api/client";
import { EXPLANATIONS, ServiceError } from "@/api/problems";
import { EvidenceList } from "@/ui/Evidence";

import { ResponseForm } from "./ResponseForm";

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
      <h2>Evidence</h2>
      <EvidenceList citations={task.evidence} />

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
        view={view}
        client={client}
        onReload={reload}
        onCommitted={(receipt) => onCommitted?.(receipt)}
      />
    </article>
  );
}
