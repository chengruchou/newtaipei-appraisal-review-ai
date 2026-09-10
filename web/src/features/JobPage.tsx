import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { JobStatusView, RevisionListView, ReviewClient, TaskListView } from "@/api/client";
import { EXPLANATIONS, ServiceError } from "@/api/problems";
import { ValueAuthority } from "@/ui/Authority";

interface Loaded {
  job: JobStatusView;
  tasks: TaskListView;
  revisions: RevisionListView;
}

type Load =
  { name: "loading" } | { name: "ready"; data: Loaded } | { name: "failed"; error: ServiceError };

const STATUS_WORDS: Record<string, string> = {
  queued: "Queued",
  dispatched: "Dispatched",
  running: "Running",
  waiting_for_human: "Waiting for a reviewer",
  retryable_failed: "Retrying after an infrastructure failure",
  failed: "Failed",
  succeeded: "Succeeded",
  cancelled: "Cancelled",
};

export function JobPage({ jobId, client }: { jobId: string; client: ReviewClient }) {
  const [load, setLoad] = useState<Load>({ name: "loading" });

  const reload = useCallback(() => {
    let cancelled = false;
    setLoad({ name: "loading" });
    Promise.all([client.readJob(jobId), client.listJobTasks(jobId), client.listJobRevisions(jobId)])
      .then(([job, tasks, revisions]) => {
        if (!cancelled) {
          setLoad({ name: "ready", data: { job, tasks, revisions } });
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
  }, [client, jobId]);

  useEffect(() => reload(), [reload]);

  if (load.name === "loading") {
    return (
      <p role="status" aria-live="polite">
        Loading this job…
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

  const { job, tasks, revisions } = load.data;
  const open = tasks.tasks.filter((view) => view.task.state === "open");
  const closed = tasks.tasks.filter((view) => view.task.state !== "open");

  return (
    <article>
      <h1>Case {job.job.case_id}</h1>
      <p>
        <strong>{STATUS_WORDS[job.job_status] ?? job.job_status}</strong>
        {job.cancel_requested ? " · cancellation requested" : null}
      </p>
      <button onClick={reload}>Refresh</button>

      <h2>Open tasks</h2>
      {open.length === 0 ? (
        <p className="muted">Nothing is waiting for you on this job.</p>
      ) : (
        <ul className="plain">
          {open.map((view) => (
            <li key={view.task.task_id} className="card">
              <Link to={`/tasks/${view.task.task_id}`}>{view.task.question}</Link>
              <p className="muted" style={{ margin: "0.25rem 0 0" }}>
                Revision {view.task.run.revision.revision_id} · version {view.task.version}
              </p>
            </li>
          ))}
        </ul>
      )}

      {closed.length === 0 ? null : (
        <>
          <h2>Already handled</h2>
          {/* Superseded tasks stay visible so a reviewer holding a stale link can see why
              it no longer applies, rather than finding it simply gone. */}
          <table>
            <thead>
              <tr>
                <th scope="col">Question</th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {closed.map((view) => (
                <tr key={view.task.task_id}>
                  <td>{view.task.question}</td>
                  <td>{view.task.state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Revision history</h2>
      <ol>
        {revisions.revisions.map((revision) => (
          <li key={revision.reference.revision_id}>
            <code>{revision.reference.revision_id}</code>
            {revision.changes.length === 0 ? (
              <span className="muted"> — no value changes</span>
            ) : (
              revision.changes.map((change) => (
                <ValueAuthority key={change.subject_id} change={change} />
              ))
            )}
          </li>
        ))}
      </ol>
    </article>
  );
}
