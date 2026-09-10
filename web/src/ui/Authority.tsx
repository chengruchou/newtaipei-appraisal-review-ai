import type { components } from "@/api/schema";

type PublicValue = components["schemas"]["PublicValue-Output"];
type ValueRevision = components["schemas"]["ValueRevision-Output"];

/**
 * Four things can be said about one cell, and they carry different authority. #25 forbids
 * mixing them, so each is labelled with who asserted it rather than rendered as one value
 * with a history. A proposal in particular is never shown as an accepted fact: it says
 * "proposed, not accepted" in its own label, not in a tooltip.
 */
const AUTHORITY = {
  observed: {
    label: "Observed",
    detail: "Extracted from the source document. Not confirmed by a reviewer.",
  },
  proposed: {
    label: "Proposed — not accepted",
    detail: "Suggested by the system. This is not a decision and nothing has been changed by it.",
  },
  corrected: {
    label: "Corrected by reviewer",
    detail: "Entered by a named human reviewer and committed as a new revision.",
  },
} as const;

export function renderValue(value: PublicValue | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (value.state !== "present") {
    return STATE_WORDS[value.state] ?? value.state;
  }
  const inner = value.value;
  if (inner === null || inner === undefined) {
    return "—";
  }
  if (typeof inner === "string") {
    return value.unit === null || value.unit === undefined ? inner : `${inner} ${value.unit}`;
  }
  const unit = inner.unit ?? value.unit;
  const shown = String(inner.value);
  return unit === null || unit === undefined ? shown : `${shown} ${unit}`;
}

const STATE_WORDS: Record<string, string> = {
  blank: "blank",
  missing: "missing from the source",
  not_present: "not present in this form",
  not_applicable: "not applicable",
};

function ValueLine({
  kind,
  value,
  by,
}: {
  kind: keyof typeof AUTHORITY;
  value: PublicValue;
  by?: string;
}) {
  const meta = AUTHORITY[kind];
  return (
    <div className="authority-row">
      <div>
        <div className="authority-label">{meta.label}</div>
        {by === undefined ? null : <div className="muted">{by}</div>}
      </div>
      <div>
        <div className="value" data-state={value.state} data-authority={kind}>
          {renderValue(value)}
        </div>
        <p className="muted" style={{ margin: "0.15rem 0 0" }}>
          {meta.detail}
        </p>
        {value.confidence === null || value.confidence === undefined ? null : (
          <p className="muted" style={{ margin: 0 }}>
            Extractor confidence {value.confidence.toFixed(2)}. A confidence is not an approval.
          </p>
        )}
      </div>
    </div>
  );
}

export function ValueAuthority({ change }: { change: ValueRevision }) {
  const corrector = change.corrected_by?.actor_id;
  return (
    <section className="card" aria-label={`Value history for ${change.subject_id}`}>
      <h3 style={{ margin: "0 0 0.5rem", fontSize: "0.95rem" }}>{change.subject_id}</h3>
      <div className="authority">
        <ValueLine kind="observed" value={change.original} />
        {change.proposed ? <ValueLine kind="proposed" value={change.proposed} /> : null}
        {change.corrected ? (
          <ValueLine
            kind="corrected"
            value={change.corrected}
            {...(corrector === undefined ? {} : { by: corrector })}
          />
        ) : null}
      </div>
    </section>
  );
}
