import { useEffect, useRef } from "react";

import type {
  HumanResponse,
  ResponseReceipt,
  ReviewClient,
  TaskView,
  TaskSubjectView,
  SourceCitation,
} from "@/api/client";
import { newIdempotencyKey } from "@/api/client";
import { ServiceError, TransportError } from "@/api/problems";
import { renderValue } from "@/ui/Authority";
import { useText } from "@/ui/Language";
import { serviceProblemText } from "@/ui/ServiceProblemText";
import { useResponseDraft, type ResponsePhase } from "./response-state";
import { statusText } from "./workbench-state";

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
  const t = useText();
  const [draft, store, currentBinding] = useResponseDraft(client, view);
  const { action, selectedCitations, correctedText, phase } = draft;
  const setAction = (value: HumanResponse["action"]) => store.change({ action: value });
  const setCorrectedText = (value: string) => store.change({ correctedText: value });
  const setPhase = (value: ResponsePhase) => store.change({ phase: value });
  const callback = useRef<((receipt: ResponseReceipt) => void) | null>(onCommitted);
  useEffect(() => {
    callback.current = onCommitted;
    return () => {
      callback.current = null;
    };
  }, [onCommitted]);
  const inFlight = phase.name === "submitting" || phase.name === "checking";
  const locked = ["confirming", "submitting", "checking", "retryable", "unreachable"].includes(
    phase.name,
  );
  const actionWords = (value: string) =>
    t(
      ACTION_WORDS[value] ?? value,
      (
        {
          confirm: "確認這一側觀察值",
          correct: "提交更正",
          supply_evidence: "補充有來源的證據",
          reject: "拒絕確認",
          approve: "核准",
          authorize_publication: "授權發布",
        } as Record<string, string>
      )[value] ?? value,
    );

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

  function accept(receipt: ResponseReceipt, command: HumanResponse) {
    if (
      receipt.task_id !== command.task_id ||
      receipt.consumed_version !== command.expected_version ||
      receipt.action !== command.action
    )
      throw new TransportError("Receipt binding mismatch");
    setPhase({ name: "committed", receipt });
    callback.current?.(receipt);
  }

  async function submit(command: HumanResponse) {
    if (!["confirming", "retryable"].includes(store.read().phase.name)) return;
    setPhase({ name: "submitting", command });
    try {
      accept(await client.submitResponse(task.task_id, command), command);
    } catch (error) {
      if (!(error instanceof ServiceError)) {
        setPhase({ name: "unreachable", command });
      } else if (error.code === "version_conflict") {
        setPhase({ name: "conflict" });
      } else {
        setPhase({ name: "refused", error });
      }
    }
  }

  async function checkOutcome(command: HumanResponse) {
    if (store.read().phase.name !== "unreachable") return;
    setPhase({ name: "checking", command });
    try {
      accept(await client.readResponse(task.task_id, command.idempotency_key), command);
    } catch (error) {
      if (error instanceof ServiceError && error.code === "not_found") {
        try {
          const current = (await client.readTask(task.task_id)).task;
          if (
            current.state !== "open" ||
            current.version !== command.expected_version ||
            JSON.stringify(current.run.revision) !== JSON.stringify(command.revision) ||
            (current.side?.input_digest ?? null) !== command.side_digest ||
            (current.result_digest ?? null) !== command.result_digest ||
            !current.allowed_responses.includes(command.action)
          ) {
            setPhase({ name: "conflict" });
          } else {
            // Absence plus current authority permits an explicit retry with the same key.
            setPhase({ name: "retryable", command });
          }
        } catch {
          setPhase({ name: "unreachable", command });
        }
      } else {
        setPhase({ name: "unreachable", command });
      }
    }
  }

  if (phase.name === "committed") return <Committed receipt={phase.receipt} />;
  if (closed && !["submitting", "unreachable", "checking"].includes(phase.name)) {
    return (
      <p className="notice" data-tone="warn">
        {t("This task is", "此任務狀態為")} <strong>{statusText(task.state, t)}</strong>
        {t(
          " and can no longer be answered. Another reviewer answered it, or a newer revision replaced the material it was asked about.",
          "，已無法回覆。任務可能已由其他審查者處理，或其材料已被新修訂取代。",
        )}
      </p>
    );
  }

  if (phase.name === "conflict") {
    return (
      <div className="notice" data-tone="danger" role="alert">
        <h3 style={{ marginTop: 0 }}>{serviceProblemText("version_conflict", t).title}</h3>
        <p>{serviceProblemText("version_conflict", t).guidance}</p>
        <button data-variant="primary" onClick={onReload}>
          {t("Reload this task", "重新讀取此任務")}
        </button>
      </div>
    );
  }

  return (
    <form
      className="response-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (
          locked ||
          correctionInvalid ||
          ["submitting", "checking", "confirming", "unreachable", "retryable"].includes(
            store.read().phase.name,
          )
        )
          return;
        const command = build();
        if (command === null) {
          return;
        }
        setPhase({ name: "confirming", command: freezeCommand(command) });
      }}
    >
      {currentBinding ? (
        <fieldset disabled={locked} style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="authority-label">{t("Your response", "您的回覆")}</legend>
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
              {actionWords(allowed)}
            </label>
          ))}

          {correctionAction ? (
            <p style={{ marginTop: "0.75rem" }}>
              <label htmlFor="corrected-value" style={{ display: "block" }}>
                {t("Corrected value", "更正值")}
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
                  ? t(
                      `Required type: ${requiredType}. Unit: ${unit ?? "not specified"}. `,
                      `所需類型：${requiredType === "number" ? "數值" : requiredType === "boolean" ? "布林值" : "文字"}。單位：${unit ?? "未指定"}。`,
                    )
                  : t(
                      "Correction unavailable: authoritative value metadata is missing. ",
                      "缺少可核對的原始值資訊，目前無法更正。",
                    )}
                {requiredType === "boolean"
                  ? t("Enter true or false. ", "請輸入 true 或 false。")
                  : null}
                {requiredType === "number" && !unit?.trim()
                  ? t(
                      "Correction blocked: required numeric unit is missing. ",
                      "缺少必要數值單位，更正已被阻擋。",
                    )
                  : null}
                {t("Recorded against", "更正對象")} <code>{view.subject_id}</code>。
                {t(
                  "Your correction is attributed to you and clears every existing confirmation on this case.",
                  "更正會記錄提交者，並清除本案既有確認；仍須重新確認與核准。",
                )}
              </span>
            </p>
          ) : null}
          {action === "supply_evidence" ? (
            <section aria-label={t("Server-provided evidence choices", "服務提供的證據選項")}>
              <p>
                {t(
                  "Select the source regions supporting this value. Only citations supplied by the service are available.",
                  "請選擇支持此值的來源區域；僅可使用服務提供的引用證據。",
                )}
              </p>
              {evidenceChoices.length === 0 ? (
                <p className="notice" data-tone="warn">
                  {t(
                    "No usable server-provided citation is available. Source admission or evidence resolution is required before this task can be answered.",
                    "服務未提供可用的引用。請先完成來源准入或補齊證據，再回覆此任務。",
                  )}
                </p>
              ) : (
                evidenceChoices.map(([key, citation]) => (
                  <label key={key} style={{ display: "block" }}>
                    <input
                      type="checkbox"
                      checked={selectedCitations.includes(key)}
                      onChange={(event) =>
                        store.change({
                          selectedCitations: event.target.checked
                            ? [...selectedCitations, key]
                            : selectedCitations.filter((value) => value !== key),
                        })
                      }
                    />
                    {t("Use", "使用")} {citation.document_id}, {t("page", "頁碼")} {citation.page},{" "}
                    {t("region", "區域")} {citation.region_id}: {citation.excerpt}
                  </label>
                ))
              )}
            </section>
          ) : null}
        </fieldset>
      ) : (
        <p className="notice" data-tone="warn">
          {t(
            "Recovering a response from an earlier task binding. Its values are not applied to the current form.",
            "正在查驗先前任務版本的提交；其值不會帶入目前表單。",
          )}
          {"command" in phase ? (
            <>
              <br />
              {t("Original revision", "原提交修訂")}：
              <code>{phase.command.revision.revision_id}</code> · {t("Task version", "任務版本")}{" "}
              {phase.command.expected_version}
            </>
          ) : null}
        </p>
      )}

      {["unreachable", "checking", "retryable"].includes(phase.name) && "command" in phase ? (
        <div className="notice" data-tone="warn" role="alert">
          <p>
            {t(
              "Your answer may or may not have been recorded. Check its receipt first; no new submission is sent while checking.",
              "回覆可能已被記錄。請先查詢原提交的收據；查詢不會重新送出。",
            )}
          </p>
          {phase.name === "retryable" ? (
            <>
              <p>
                {t(
                  "No receipt was found and the task is still unchanged. You may explicitly resend the same frozen response and key.",
                  "尚查無收據，且任務仍為相同版本。可明確選擇以原固定內容重送，並沿用原提交識別鍵以避免重複記錄。",
                )}
              </p>
              <button
                type="button"
                data-variant="primary"
                onClick={() => void submit(phase.command)}
              >
                {t("Send again", "以同一內容重送")}
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={inFlight}
              data-variant="primary"
              onClick={() => void checkOutcome(phase.command)}
            >
              {phase.name === "checking"
                ? t("Checking submission…", "正在查詢提交狀態…")
                : t("Check submission status", "查詢提交狀態")}
            </button>
          )}
        </div>
      ) : null}

      {phase.name === "refused" ? (
        <div className="notice" data-tone="danger" role="alert">
          <h3 style={{ marginTop: 0 }}>{serviceProblemText(phase.error.code, t).title}</h3>
          <p>{serviceProblemText(phase.error.code, t).guidance}</p>
        </div>
      ) : null}

      {phase.name === "confirming" || phase.name === "submitting" ? (
        <div
          className="notice response-confirmation"
          data-tone="warn"
          aria-label={t("Confirm response", "送出前確認")}
        >
          <p>
            {t("Submit", "準備提交")} <strong>{actionWords(phase.command.action)}</strong>
            {t(
              " for this task? This records your response against revision ",
              "。此回覆會記錄於案件修訂 ",
            )}
            <code>{phase.command.revision.revision_id}</code>。
            {phase.command.correction ? (
              <span>
                {" "}
                {t("Original", "原始值")}: {renderValue(phase.command.correction.original, t)}.{" "}
                {t("Proposed correction (not accepted)", "擬更正值（尚未採納）")}:{" "}
                {renderValue(phase.command.correction.proposed, t)}. {t("Entered text", "輸入原文")}
                : {phase.command.correction.proposed?.raw_text}.
              </span>
            ) : null}
          </p>
          {phase.command.action === "supply_evidence" ? (
            <ul aria-label={t("Evidence in the confirmed command", "本次待提交內容的證據")}>
              {phase.command.correction?.proposed?.evidence.map((citation) => (
                <li key={citationKey(citation)}>
                  {citation.document_id}, {t("page", "頁碼")} {citation.page}, {t("region", "區域")}{" "}
                  {citation.region_id}: {citation.excerpt}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="small">
            {t(
              "Only the response shown here will be sent. A correction does not grant approval or make a report ready.",
              "只會送出此處列出的回覆內容。更正不會自動核准，也不表示報表已就緒。",
            )}
          </p>
          <div className="response-actions">
            <button
              type="button"
              data-variant="primary"
              disabled={inFlight}
              onClick={() => void submit(phase.command)}
            >
              {inFlight ? t("Submitting…", "正在提交…") : t("Yes, submit", "確認送出")}
            </button>{" "}
            <button type="button" disabled={inFlight} onClick={() => setPhase({ name: "editing" })}>
              {t("Go back", "返回編輯")}
            </button>
          </div>
        </div>
      ) : ["unreachable", "checking", "retryable"].includes(phase.name) ? null : (
        <button type="submit" data-variant="primary" disabled={inFlight || correctionInvalid}>
          {t("Review and submit", "檢視並準備提交")}
        </button>
      )}
    </form>
  );
}

function Committed({ receipt }: { receipt: ResponseReceipt }) {
  const t = useText();
  return (
    <div className="notice" data-tone="ok" role="status">
      <h3 style={{ marginTop: 0 }}>{t("Response recorded", "回覆已記錄")}</h3>
      {receipt.revision ? (
        <p>
          {t("Committed as revision", "已提交為新修訂")} <code>{receipt.revision.revision_id}</code>
          . {t("A new review run is scheduled; the job is now", "已排程續行，目前工作狀態為")}{" "}
          <strong>{statusText(receipt.job_status, t)}</strong>.
        </p>
      ) : (
        <p>
          {t(
            "Recorded. No material changed, so no new revision was created and the job stays",
            "已記錄；未改變材料，因此沒有新修訂，目前工作狀態為",
          )}{" "}
          <strong>{statusText(receipt.job_status, t)}</strong>.
        </p>
      )}
      {receipt.superseded_task_ids.length > 0 ? (
        <p>
          {t(
            `${receipt.superseded_task_ids.length} other task${receipt.superseded_task_ids.length === 1 ? " was" : "s were"} superseded, because they asked about material this revision replaced.`,
            `另有 ${receipt.superseded_task_ids.length} 個任務因相關材料已被此修訂取代而失效。`,
          )}
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
