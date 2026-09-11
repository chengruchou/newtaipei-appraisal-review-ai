import { useText } from "./Language";
import type { components } from "@/api/schema";

type PublicValue = components["schemas"]["PublicValue-Input"];
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

export function renderValue(
  value: PublicValue | null | undefined,
  t: (english: string, chinese: string) => string = (english) => english,
): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (value.state !== "present") {
    const words = STATE_WORDS[value.state];
    return words ? t(...words) : value.state;
  }
  const inner = value.value;
  if (inner === null || inner === undefined) {
    return "—";
  }
  if (typeof inner === "string" || typeof inner === "number") {
    return value.unit === null || value.unit === undefined
      ? String(inner)
      : `${inner} ${value.unit}`;
  }
  const unit = inner.unit ?? value.unit;
  const shown = String(inner.value);
  return unit === null || unit === undefined ? shown : `${shown} ${unit}`;
}

const STATE_WORDS: Record<string, [string, string]> = {
  blank: ["blank", "原欄位空白"],
  missing: ["missing from the source", "來源中缺少此值"],
  not_present: ["not present in this form", "此表單無此欄位"],
  not_applicable: ["not applicable", "不適用"],
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
  const t = useText();
  const meta = AUTHORITY[kind];
  const zh = {
    observed: ["原始觀察值", "來源文件的擷取內容；人工確認與核准須另依精確綁定紀錄判斷。"],
    proposed: ["建議值，尚未採納", "這只是提案，不是已接受的決定，也不會自行修改材料。"],
    corrected: ["人工更正值", "由具權限的審查者提交為新修訂，不會自動確認或核准。"],
  }[kind];
  return (
    <div className="authority-row">
      <div>
        <div className="authority-label">{t(meta.label, zh[0]!)}</div>
        {by === undefined ? null : <div className="muted">{by}</div>}
      </div>
      <div>
        <div className="value" data-state={value.state} data-authority={kind}>
          {renderValue(value, t)}
        </div>
        <p className="muted" style={{ margin: "0.15rem 0 0" }}>
          {t(meta.detail, zh[1]!)}
        </p>
        {value.confidence === null || value.confidence === undefined ? null : (
          <p className="muted" style={{ margin: 0 }}>
            {t("Extractor confidence", "原始擷取信心值")} {value.confidence}.{" "}
            {t("A confidence is not an approval.", "信心值不是核准。")}
          </p>
        )}
      </div>
    </div>
  );
}

export function ValueAuthority({ change }: { change: ValueRevision }) {
  const t = useText();
  const corrector = change.corrected_by?.actor_id;
  return (
    <section
      className="card"
      aria-label={t(
        `Value history for ${change.subject_id}`,
        `${change.subject_id} 的值與來源角色`,
      )}
    >
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
