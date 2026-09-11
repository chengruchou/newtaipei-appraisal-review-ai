import { useState } from "react";

import type {
  HumanResponse,
  ResponseReceipt,
  ReviewClient,
  TaskView,
  TaskSubjectView,
  SourceCitation,
} from "@/api/client";
import { newIdempotencyKey } from "@/api/client";
import { EXPLANATIONS, ServiceError, TransportError } from "@/api/problems";
import { renderValue } from "@/ui/Authority";

type Phase =
  | { name: "editing" }
  | { name: "confirming"; command: HumanResponse }
  | { name: "submitting"; command: HumanResponse }
  | { name: "committed"; receipt: ResponseReceipt }
  | { name: "conflict" }
  | { name: "unreachable"; message: string; command: HumanResponse }
  | { name: "refused"; error: ServiceError };

const ACTION_WORDS: Record<string, string> = {
  confirm: "Confirm this observation",
  correct: "Submit a correction",
  supply_evidence: "Supply cited evidence",
  reject: "Refuse to confirm",
  approve: "Approve",
  authorize_publication: "Authorize publication",
};

export interface ResponseFormProps {
  view: TaskView;
  subject?: TaskSubjectView | null;
  client: ReviewClient;
  onCommitted: (receipt: ResponseReceipt) => void;
  onReload: () => void;
  /** Injected so a test can pin the key instead of reaching for crypto.randomUUID. */
  mintKey?: () => string;
}

export function ResponseForm({
  view,
  subject = null,
  client,
  onCommitted,
  onReload,
  mintKey = newIdempotencyKey,
}: ResponseFormProps) {
  const task = view.task;
  const [action, setAction] = useState<HumanResponse["action"]>(
    task.allowed_responses[0] ?? "reject",
  );
  const [selectedCitations, setSelectedCitations] = useState<string[]>([]);
  const [correctedText, setCorrectedText] = useState("");
  const [phase, setPhase] = useState<Phase>({ name: "editing" });
  const inFlight = phase.name === "submitting";
  const locked = ["confirming", "submitting", "unreachable"].includes(phase.name);

  const closed = task.state !== "open";
  const subjectValid = subject !== null && subjectMatches(view, subject);
  const requiredType = subjectValid ? subject.required_type : null;
  const unit = subjectValid ? subject.required_unit : null;
  const numeric = Number(correctedText);
  const correctionAction = action === "correct" || action === "supply_evidence";
  const evidenceChoices = [
    ...new Map(
      [...task.evidence, ...(subjectValid ? subject.observation.evidence : [])]
        .filter(usableCitation)
        .map((citation) => [citationKey(citation), citation] as const),
    ).entries(),
  ];
  const suppliedEvidence = evidenceChoices
    .filter(([key]) => selectedCitations.includes(key))
    .map(([, citation]) => citation);
  const correctionInvalid =
    correctionAction &&
    ((action === "supply_evidence" && suppliedEvidence.length === 0) ||
      !subjectValid ||
      requiredType === null ||
      ((subject.unit_required || requiredType === "number") && !unit?.trim()) ||
      correctedText.trim() === "" ||
      (requiredType === "number" &&
        (!Number.isFinite(numeric) ||
          !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(correctedText.trim()))) ||
      (requiredType === "boolean" && !["true", "false"].includes(correctedText)));

  function build(): HumanResponse | null {
    const base = {
      schema_version: "service-v1",
      task_id: task.task_id,
      expected_version: task.version,
      revision: task.run.revision,
      side_digest: task.side?.input_digest ?? null,
      result_digest: task.result_digest ?? null,
      idempotency_key: mintKey(),
      action,
      correction: null,
    } satisfies HumanResponse;
    if (!correctionAction) {
      return base;
    }
    if (correctionInvalid || !subjectValid || requiredType === null) {
      return null;
    }
    return {
      ...base,
      // The subject name comes from the server. Rebuilding it here would canonicalize the
      // comparison context differently for any case identified in Chinese.
      correction: {
        schema_version: "service-v1",
        subject_id: subject.subject_id,
        original: subject.observation,
        proposed: {
          schema_version: "service-v1",
          state: "present",
          value: {
            type: requiredType,
            value:
              requiredType === "number"
                ? numeric
                : requiredType === "boolean"
                  ? correctedText === "true"
                  : correctedText,
            unit,
          },
          raw_text: correctedText,
          unit,
          confidence: subject.observation.confidence ?? null,
          evidence:
            action === "supply_evidence" ? suppliedEvidence : (subject.observation.evidence ?? []),
        },
        corrected: null,
        corrected_by: null,
      },
    };
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
        setPhase({ name: "unreachable", message: error.message, command });
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
        if (locked || correctionInvalid) return;
        const command = build();
        if (command === null) {
          return;
        }
        setPhase({ name: "confirming", command: freezeCommand(command) });
      }}
    >
      <fieldset disabled={locked} style={{ border: 0, padding: 0, margin: 0 }}>
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

        {correctionAction ? (
          <p style={{ marginTop: "0.75rem" }}>
            <label htmlFor="corrected-value" style={{ display: "block" }}>
              Corrected value
            </label>
            <input
              id="corrected-value"
              inputMode={requiredType === "number" ? "decimal" : "text"}
              value={correctedText}
              aria-describedby="corrected-help"
              onChange={(event) => setCorrectedText(event.target.value)}
            />
            <span id="corrected-help" className="muted">
              {requiredType
                ? `Required type: ${requiredType}. Unit: ${unit ?? "not specified"}. `
                : "Correction unavailable: authoritative value metadata is missing. "}
              {requiredType === "boolean" ? "Enter true or false. " : null}
              {requiredType === "number" && !unit?.trim()
                ? "Correction blocked: required numeric unit is missing. "
                : null}
              Recorded against {view.subject_id}. Your correction is attributed to you and clears
              every existing confirmation on this case.
            </span>
          </p>
        ) : null}
        {action === "supply_evidence" ? (
          <section aria-label="Server-provided evidence choices">
            <p>
              Select the source regions supporting this value. Only citations supplied by the
              service are available.
            </p>
            {evidenceChoices.length === 0 ? (
              <p className="notice" data-tone="warn">
                No usable server-provided citation is available. Source admission or evidence
                resolution is required before this task can be answered.
              </p>
            ) : (
              evidenceChoices.map(([key, citation]) => (
                <label key={key} style={{ display: "block" }}>
                  <input
                    type="checkbox"
                    checked={selectedCitations.includes(key)}
                    onChange={(event) =>
                      setSelectedCitations((previous) =>
                        event.target.checked
                          ? [...previous, key]
                          : previous.filter((value) => value !== key),
                      )
                    }
                  />
                  Use {citation.document_id}, page {citation.page}, region {citation.region_id}:{" "}
                  {citation.excerpt}
                </label>
              ))
            )}
          </section>
        ) : null}
      </fieldset>

      {phase.name === "unreachable" ? (
        <div className="notice" data-tone="warn" role="alert">
          <p>
            {phase.message} Your answer may or may not have been recorded. Sending it again is safe:
            it carries the same submission key, so the service will either accept it once or return
            the answer it already has.
          </p>
          <button type="button" data-variant="primary" onClick={() => void submit(phase.command)}>
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
            Submit <strong>{ACTION_WORDS[phase.command.action] ?? phase.command.action}</strong> for
            this task? This records your response against revision{" "}
            {phase.command.revision.revision_id}.
            {phase.command.correction ? (
              <span>
                {" "}
                Original: {renderValue(phase.command.correction.original)}. Proposed correction (not
                accepted): {renderValue(phase.command.correction.proposed)}. Entered text:{" "}
                {phase.command.correction.proposed?.raw_text}.
              </span>
            ) : null}
          </p>
          {phase.command.action === "supply_evidence" ? (
            <ul aria-label="Evidence in the confirmed command">
              {phase.command.correction?.proposed?.evidence.map((citation) => (
                <li key={citationKey(citation)}>
                  {citation.document_id}, page {citation.page}, region {citation.region_id}:{" "}
                  {citation.excerpt}
                </li>
              ))}
            </ul>
          ) : null}
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
      ) : phase.name === "unreachable" ? null : (
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

/** Detach and freeze the exact reviewed command, including nested correction evidence. */
function freezeCommand(command: HumanResponse): HumanResponse {
  const snapshot = structuredClone(command);
  function freeze(value: unknown): void {
    if (value && typeof value === "object") {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
  }
  freeze(snapshot);
  return snapshot;
}

export function subjectMatches(view: TaskView, subject: TaskSubjectView): boolean {
  const expected = view.task.run.revision;
  return (
    subject.task_id === view.task.task_id &&
    subject.subject_id === view.subject_id &&
    subject.revision.case_id === expected.case_id &&
    subject.revision.revision_id === expected.revision_id &&
    subject.revision.material_digest === expected.material_digest
  );
}

function citationKey(citation: SourceCitation): string {
  return JSON.stringify([
    citation.document_id,
    citation.version,
    citation.content_hash,
    citation.page,
    citation.region_id,
    citation.bbox,
    citation.excerpt,
  ]);
}
function usableCitation(citation: SourceCitation): boolean {
  const [x0, y0, x1, y1] = citation.bbox;
  return (
    citation.document_id.length > 0 &&
    citation.version.length > 0 &&
    citation.region_id.length > 0 &&
    /^[a-f0-9]{64}$/.test(citation.content_hash) &&
    Number.isInteger(citation.page) &&
    citation.page >= 1 &&
    [x0, y0, x1, y1].every(Number.isFinite) &&
    x0 >= 0 &&
    y0 >= 0 &&
    x1 > x0 &&
    y1 > y0
  );
}
