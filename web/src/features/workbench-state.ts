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
