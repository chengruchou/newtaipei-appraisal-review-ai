import { useMemo, useRef, useState } from "react";

import type { HumanResponse, ResponseReceipt, ReviewClient, TaskView } from "@/api/client";
import { newIdempotencyKey } from "@/api/client";
import { EXPLANATIONS, ServiceError, TransportError } from "@/api/problems";

type Phase =
  | { name: "editing" }
  | { name: "confirming"; command: HumanResponse }
  | { name: "submitting"; command: HumanResponse }
  | { name: "committed"; receipt: ResponseReceipt }
  | { name: "conflict" }
  | { name: "unreachable"; message: string }
  | { name: "refused"; error: ServiceError };

const ACTION_WORDS: Record<string, string> = {
  confirm: "Confirm this observation",
  correct: "Submit a correction",
  reject: "Refuse to confirm",
  approve: "Approve",
  authorize_publication: "Authorize publication",
};

export interface ResponseFormProps {
  view: TaskView;
  client: ReviewClient;
  onCommitted: (receipt: ResponseReceipt) => void;
  onReload: () => void;
  /** Injected so a test can pin the key instead of reaching for crypto.randomUUID. */
  mintKey?: () => string;
}

export function ResponseForm({
  view,
  client,
  onCommitted,
  onReload,
  mintKey = newIdempotencyKey,
}: ResponseFormProps) {
  const task = view.task;
  // Minted once per mounted form, so every retry of this one decision reuses it. Minting
  // at submit time would turn a retry after a timeout into a second distinct write.
  const idempotencyKey = useRef<string>(mintKey()).current;
  const [action, setAction] = useState<string>(task.allowed_responses[0] ?? "");
  const [correctedText, setCorrectedText] = useState("");
  const [phase, setPhase] = useState<Phase>({ name: "editing" });
  const inFlight = phase.name === "submitting";

  const closed = task.state !== "open";
  const numeric = useMemo(() => Number(correctedText), [correctedText]);
  const correctionInvalid =
    action === "correct" && (correctedText.trim() === "" || !Number.isFinite(numeric));

  function build(): HumanResponse | null {
    const base = {
      schema_version: "service-v1",
      task_id: task.task_id,
      expected_version: task.version,
      revision: task.run.revision,
      side_digest: task.side?.input_digest ?? null,
      result_digest: task.result_digest ?? null,
      idempotency_key: idempotencyKey,
      action,
      correction: null,
    } as unknown as HumanResponse;
    if (action !== "correct") {
      return base;
    }
    if (view.subject_id === null || view.subject_id === undefined) {
      return null;
    }
    return {
      ...base,
      // The subject name comes from the server. Rebuilding it here would canonicalize the
      // comparison context differently for any case identified in Chinese.
      correction: {
        schema_version: "service-v1",
        subject_id: view.subject_id,
        original: { schema_version: "service-v1", state: "blank", raw_text: "" },
        proposed: {
          schema_version: "service-v1",
          state: "present",
          value: { type: "number", value: numeric, unit: task.side?.factor_id ? null : null },
          raw_text: correctedText,
          unit: null,
          confidence: null,
          evidence: [],
        },
        corrected: null,
        corrected_by: null,
      },
    } as unknown as HumanResponse;
  }

  async function submit(command: HumanResponse) {
    setPhase({ name: "submitting", command });
    try {
      const receipt = await client.submitResponse(task.task_id, command);
      setPhase({ name: "committed", receipt });
      onCommitted(receipt);
    } catch (error) {
      if (error instanceof TransportError) {
        // The write may or may not have applied. The key makes resending safe, so offer
        // exactly that rather than telling the reviewer to start again.
        setPhase({ name: "unreachable", message: error.message });
        return;
      }
      if (error instanceof ServiceError && error.code === "version_conflict") {
        setPhase({ name: "conflict" });
        return;
      }
      setPhase({
        name: "refused",
        error: error instanceof ServiceError ? error : new ServiceError("execution_failed", 500),
      });
    }
  }

  if (closed) {
    return (
      <p className="notice" data-tone="warn">
        This task is <strong>{task.state}</strong> and can no longer be answered. Another reviewer
        answered it, or a newer revision replaced the material it was asked about.
      </p>
    );
  }

  if (phase.name === "committed") {
    return <Committed receipt={phase.receipt} />;
  }

  if (phase.name === "conflict") {
    return (
      <div className="notice" data-tone="danger" role="alert">
        <h3 style={{ marginTop: 0 }}>{EXPLANATIONS.version_conflict.title}</h3>
        <p>{EXPLANATIONS.version_conflict.guidance}</p>
        <button data-variant="primary" onClick={onReload}>
          Reload this task
        </button>
      </div>
    );
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const command = build();
        if (command === null) {
          return;
        }
        setPhase({ name: "confirming", command });
      }}
    >
      <fieldset disabled={inFlight} style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="authority-label">Your response</legend>
        {/* Only what the task itself allows. The form never offers an action the server
            would reject, and never invents one the schema does not list. */}
        {task.allowed_responses.map((allowed) => (
          <label key={allowed} style={{ display: "block", padding: "0.25rem 0" }}>
            <input
              type="radio"
              name="action"
              value={allowed}
              checked={action === allowed}
              onChange={() => setAction(allowed)}
            />{" "}
            {ACTION_WORDS[allowed] ?? allowed}
          </label>
        ))}

        {action === "correct" ? (
          <p style={{ marginTop: "0.75rem" }}>
            <label htmlFor="corrected-value" style={{ display: "block" }}>
              Corrected value
            </label>
            <input
              id="corrected-value"
              inputMode="decimal"
              value={correctedText}
              aria-describedby="corrected-help"
              onChange={(event) => setCorrectedText(event.target.value)}
            />
            <span id="corrected-help" className="muted">
              Recorded against {view.subject_id}. Your correction is attributed to you and clears
              every existing confirmation on this case.
            </span>
          </p>
        ) : null}
      </fieldset>

      {phase.name === "unreachable" ? (
        <div className="notice" data-tone="warn" role="alert">
          <p>
            {phase.message} Your answer may or may not have been recorded. Sending it again is safe:
            it carries the same submission key, so the service will either accept it once or return
            the answer it already has.
          </p>
          <button
            type="button"
            data-variant="primary"
            onClick={() => {
              const command = build();
              if (command !== null) {
                void submit(command);
              }
            }}
          >
            Send again
          </button>
        </div>
      ) : null}

      {phase.name === "refused" ? (
        <div className="notice" data-tone="danger" role="alert">
          <h3 style={{ marginTop: 0 }}>{EXPLANATIONS[phase.error.code].title}</h3>
          <p>{EXPLANATIONS[phase.error.code].guidance}</p>
        </div>
      ) : null}

      {phase.name === "confirming" || phase.name === "submitting" ? (
        <div className="notice" data-tone="warn">
          <p>
            Submit <strong>{ACTION_WORDS[action] ?? action}</strong> for this task? This is recorded
            against your name and creates a new revision.
          </p>
          <button
            type="button"
            data-variant="primary"
            disabled={inFlight}
            onClick={() => void submit(phase.command)}
          >
            {inFlight ? "Submitting…" : "Yes, submit"}
          </button>{" "}
          <button type="button" disabled={inFlight} onClick={() => setPhase({ name: "editing" })}>
            Go back
          </button>
        </div>
      ) : (
        <button type="submit" data-variant="primary" disabled={inFlight || correctionInvalid}>
          Review and submit
        </button>
      )}
    </form>
  );
}

function Committed({ receipt }: { receipt: ResponseReceipt }) {
  return (
    <div className="notice" data-tone="ok" role="status">
      <h3 style={{ marginTop: 0 }}>Response recorded</h3>
      {receipt.revision ? (
        <p>
          Committed as revision <code>{receipt.revision.revision_id}</code>. A new review run is
          scheduled; the job is now <strong>{receipt.job_status}</strong>.
        </p>
      ) : (
        <p>
          Recorded. No material changed, so no new revision was created and the job stays{" "}
          <strong>{receipt.job_status}</strong>.
        </p>
      )}
      {receipt.superseded_task_ids.length > 0 ? (
        <p>
          {receipt.superseded_task_ids.length} other task
          {receipt.superseded_task_ids.length === 1 ? " was" : "s were"} superseded, because they
          asked about material this revision replaced.
        </p>
      ) : null}
    </div>
  );
}
