import type { ServiceResult } from "@/api/client";

type Translate = (english: string, chinese: string) => string;
export function statusText(status: string, t: Translate): string {
  const words: Record<string, [string, string]> = {
    queued: ["Queued", "已排隊"],
    dispatched: ["Dispatched", "已派送"],
    running: ["Running", "執行中"],
    waiting_for_human: ["Waiting for a reviewer", "等待人工處理"],
    retryable_failed: ["Execution interrupted; retry eligible", "執行中斷，可重試"],
    failed: ["Failed", "執行失敗"],
    succeeded: ["Execution succeeded", "執行成功"],
    cancelled: ["Cancelled", "已取消"],
    verified: ["Verified", "檢核相符"],
    needs_review: ["Needs review", "需要人工核對"],
    completed: ["Completed", "本輪處理完成"],
    received: ["Received", "已收件"],
    open: ["Open", "待回覆"],
    answered: ["Answered", "已回覆"],
    superseded: ["Superseded", "已被取代"],
    expired: ["Expired", "已過期"],
    candidate: ["Candidate", "候選，尚未核准"],
    approved: ["Approved declaration", "已標記核准"],
    rejected: ["Rejected", "已拒絕"],
    unknown: ["Unknown", "尚未提供"],
    written: ["Written", "已寫出"],
    not_requested: ["Not requested", "未要求產出"],
    blocked: ["Blocked", "產出受阻"],
  };
  const pair = words[status];
  return pair ? t(...pair) : status;
}

export function documentPurposeText(purpose: string, t: Translate): string {
  const words: Record<string, [string, string]> = {
    forms: ["Case input / existing report", "個案輸入／既有報表"],
    criteria: ["Rule source", "規則來源"],
    template: ["Report template", "報表模板"],
    reference: ["Reference document", "參考文件"],
    brief: ["Competition brief", "競賽命題文件"],
  };
  const pair = words[purpose];
  return pair ? t(...pair) : purpose;
}

export function findingKindText(kind: string, t: Translate): string {
  const labels: Record<string, [string, string]> = {
    approval: ["Material authority", "材料核准狀態"],
    source_identity: ["Source identity", "來源版本與雜湊"],
    rule_status: ["Rule review state", "規則核對狀態"],
    rule_source: ["Rule source binding", "規則來源綁定"],
    rule_approval: ["Rule authority", "規則核准狀態"],
    evidence_reliability: ["Observation needs confirmation", "欄位觀察待核對"],
    observed_comparison: ["Reported value comparison", "填報值比對"],
    not_applicable: ["Rule applicability", "規則適用條件"],
    inventory: ["Required review coverage", "必要檢核覆蓋"],
  };
  const label = labels[kind];
  return label ? t(...label) : kind;
}

export function taskQuestionText(
  reason: string | null | undefined,
  question: string,
  t: Translate,
): string {
  return reason === "local-source-observation"
    ? t(
        question,
        "請先對照本側觀察值與引用原件，再明確確認或拒絕。確認此欄位不代表規則或完整材料已核准。",
      )
    : question;
}

export type Finding = ServiceResult["findings"][number];
export type FindingCategory = "matched" | "content" | "rules" | "evidence" | "uncovered";
const RULE_KINDS = new Set([
  "not_applicable",
  "selection_conflict",
  "rule_source",
  "rule_rejected",
  "rule_status",
  "approval",
  "rule_approval",
]);
const UNCOVERED_KINDS = new Set([
  "unknown_context",
  "unknown_observed",
  "unsupported",
  "unsupported_contexts",
  "inventory",
  "unresolved",
  "arithmetic_dependency",
  "observed_missing",
  "page_coverage",
  "table_coverage",
  "rule_coverage",
  "missing_factor",
  "unknown_factor",
]);

/** Classify only established finding meanings; an unknown failure is not a proven value error. */
export function findingCategory(finding: Finding): FindingCategory {
  if (finding.status === "verified") return "matched";
  if (RULE_KINDS.has(finding.kind)) return "rules";
  if (
    finding.kind === "observed_comparison" &&
    finding.status === "failed" &&
    hasComparisonValue(finding.observed) &&
    hasComparisonValue(finding.expected)
  )
    return "content";
  if (UNCOVERED_KINDS.has(finding.kind)) return "uncovered";
  return "evidence";
}

function hasComparisonValue(value: string | null | undefined): boolean {
  return value != null && value.trim() !== "" && value !== "None";
}

export function categoryLabel(category: FindingCategory, t: Translate): string {
  const labels: Record<FindingCategory, [string, string]> = {
    matched: ["Matched / passed", "相符／通過"],
    content: ["Content mismatch", "內容不一致"],
    rules: ["Rules / authority need review", "規則／權限待確認"],
    evidence: ["Evidence needs review", "證據不足／待核對"],
    uncovered: ["Uncovered / unresolved", "未覆蓋／未決"],
  };
  return t(...labels[category]);
}

export type SubjectRole = "target" | "comparable";

/** Roles come from the service's own identifiers, never from document page order. */
export function subjectRoleText(role: SubjectRole, t: Translate): string {
  return role === "target" ? t("Target", "比準地") : t("Comparable", "比較標的");
}

export type BlockerKind =
  | "coverage_missing"
  | "coverage_unsupported"
  | "verification_critical"
  | "verification_warning"
  | "selection_missing"
  | "selection_ambiguous"
  | "rule_unapproved"
  | "source_unresolved";

export function blockerKindText(kind: BlockerKind, t: Translate): string {
  const words: Record<BlockerKind, [string, string]> = {
    coverage_missing: ["Required check not covered", "必要檢核未覆蓋"],
    coverage_unsupported: ["Required check unsupported", "必要檢核未支援"],
    verification_critical: ["Verification blocker", "獨立驗證阻擋"],
    verification_warning: ["Verification warning", "獨立驗證警示"],
    selection_missing: ["No applicable rule version", "找不到適用規則版本"],
    selection_ambiguous: ["Conflicting rule versions", "適用規則版本衝突"],
    rule_unapproved: ["Rule not approved", "規則尚未核准"],
    source_unresolved: ["Unresolved source metadata", "來源目錄資料未解決"],
  };
  return t(...words[kind]);
}

/** What the reviewer can actually do next. Never a claim that the service will act. */
export function blockerActionText(kind: BlockerKind, t: Translate): string {
  const words: Record<BlockerKind, [string, string]> = {
    coverage_missing: [
      "Supply this field's observation and source through the matching human task.",
      "透過對應人工作業補齊此欄位的觀察值與來源引用。",
    ],
    coverage_unsupported: [
      "This deployment cannot check this item. The operator decides the rule scope; the frontend does not substitute one.",
      "本部署無法檢核此項目，規則範圍須由管理者確認，前端不會自行替代。",
    ],
    verification_critical: [
      "Read the current findings and the open human tasks before any publication decision.",
      "在做出任何發布決定前，先檢視本輪發現與待處理人工作業。",
    ],
    verification_warning: [
      "Request human review of this warning; it is not cleared by reloading.",
      "此警示須由人工複核，重新整理不會將其清除。",
    ],
    selection_missing: [
      "Confirm the applicable rule version for this comparison. No other district's rules are substituted.",
      "確認此比較情境的適用規則版本；不會改用其他行政區的規則。",
    ],
    selection_ambiguous: [
      "Resolve which pinned rule version applies to this comparison.",
      "釐清此比較情境應適用哪一個已固定的規則版本。",
    ],
    rule_unapproved: [
      "Rule approval is a separate permission. Confirming an observation does not approve a rule.",
      "規則核准是獨立權限；確認欄位觀察值不等於已核准規則。",
    ],
    source_unresolved: [
      "The catalog metadata for this source is still unresolved; the operator has to settle it.",
      "此來源的目錄資料尚未解決，須由管理者處理。",
    ],
  };
  return t(...words[kind]);
}

export interface OfficialFormDefinition {
  /** Stable local identifier; not a service artifact identifier. */
  id: "form3" | "form4" | "form5";
  officialName: string;
  visibleSheet: string;
  /** Present only where the official filename carries a land-use qualifier. */
  landUseQualifier: string | null;
  granularity: "section" | "comparison";
  /** How the workbook stores a difference or correction rate. The frontend never converts. */
  rateConvention: [string, string];
}

/**
 * Read-only facts confirmed against the operator's own workbooks on 2026-09-12. The
 * files stay out of version control; only their identity and unit conventions are
 * recorded here so a label cannot silently describe the wrong sheet.
 */
export const OFFICIAL_FORMS: OfficialFormDefinition[] = [
  {
    id: "form3",
    officialName: "地價區段勘查表",
    visibleSheet: "表3區段勘查表",
    landUseQualifier: null,
    granularity: "section",
    rateConvention: [
      "No difference-rate column is published for this form here.",
      "此表未在此登錄差異率欄位慣例。",
    ],
  },
  {
    id: "form4",
    officialName: "比較法調查估價表",
    visibleSheet: "表4比較法調查估價表",
    landUseQualifier: null,
    granularity: "comparison",
    rateConvention: [
      "Difference-rate columns use an Excel percent format: five points is stored as 0.05 and displays as 5.00%.",
      "差異率欄位採 Excel 百分比格式：五個百分點儲存為 0.05，顯示為 5.00%。",
    ],
  },
  {
    id: "form5",
    officialName: "影響地價區域因素分析明細表（住宅用地）",
    visibleSheet: "表5-1區域因素明細表(住)",
    landUseQualifier: "住宅用地",
    granularity: "comparison",
    rateConvention: [
      "Correction-percentage columns use a plain numeric format: five points is stored as 5, not 0.05.",
      "修正率欄位採一般數值格式：五個百分點儲存為 5，而不是 0.05。",
    ],
  },
];

/** A bare form number would mislabel the sheet after a district or land-use change. */
export function officialFormLabel(form: OfficialFormDefinition, t: Translate): string {
  const numbers: Record<OfficialFormDefinition["id"], [string, string]> = {
    form3: ["Form 3", "表 3"],
    form4: ["Form 4", "表 4"],
    form5: ["Form 5-1", "表 5-1"],
  };
  const number = t(...numbers[form.id]);
  return form.landUseQualifier && !form.officialName.includes(form.landUseQualifier)
    ? `${number} · ${form.officialName}（${form.landUseQualifier}）`
    : `${number} · ${form.officialName}`;
}

export function officialFormGranularityText(form: OfficialFormDefinition, t: Translate): string {
  return form.granularity === "section"
    ? t(
        "One file per land value section. The number of files depends on how many sections the subjects fall in.",
        "以地價區段為單位，份數取決於標的分布於幾個區段。",
      )
    : t(
        "One target plus comparable 1, 2 and 3, as the official layout fixes the axis.",
        "固定為一個比準地加上比較標的 1、2、3，與官方版面一致。",
      );
}

export type ConditionField =
  | "case_id"
  | "district"
  | "zone"
  | "land_use_category"
  | "effective_date"
  | "current_use"
  | "regulatory_zone"
  | "target_id"
  | "comparable_id";

export function conditionFieldText(field: ConditionField, t: Translate): string {
  const words: Record<ConditionField, [string, string]> = {
    case_id: ["Case", "案件"],
    district: ["District", "行政區"],
    zone: ["Section", "區段"],
    land_use_category: ["Rule use category", "規則用途分類"],
    effective_date: ["Applicable date", "適用日期"],
    current_use: ["Current use", "現況用途"],
    regulatory_zone: ["Regulatory zoning", "法定使用分區"],
    target_id: ["Target", "比準地"],
    comparable_id: ["Comparable", "比較標的"],
  };
  return t(...words[field]);
}

export function conditionMethodText(
  method: "native_proposed" | "manual_proposed",
  t: Translate,
): string {
  return method === "native_proposed"
    ? t("Native parsed candidate", "原生解析候選")
    : t("Manual interpretation candidate", "人工解讀候選");
}
