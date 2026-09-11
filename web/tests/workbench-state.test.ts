/** Unit regression of presentation categories; no real-case acceptance evidence. */
import { describe, expect, it } from "vitest";
import {
  findingCategory,
  categoryLabel,
  statusText,
  type Finding,
} from "@/features/workbench-state";

function finding(patch: Partial<Finding> = {}): Finding {
  return {
    id: "unit-finding",
    kind: "observed_comparison",
    status: "failed",
    trace: "Unit classification input",
    observed: "10",
    expected: "12",
    ...patch,
  };
}

describe("finding presentation categories (unit regression)", () => {
  it("uses an explicit verified status for matched records", () => {
    expect(findingCategory(finding({ kind: "rule_source", status: "verified" }))).toBe("matched");
  });
  it("requires failed comparison and two available values for content mismatch", () => {
    expect(findingCategory(finding())).toBe("content");
    expect(findingCategory(finding({ observed: "0", expected: "1" }))).toBe("content");
    expect(findingCategory(finding({ status: "needs_review" }))).toBe("evidence");
  });
  it.each([undefined, null, "", "  ", "None"])(
    "does not turn unavailable value %s into a mismatch",
    (value) => {
      const incomplete = finding();
      if (value === undefined) {
        delete incomplete.observed;
        delete incomplete.expected;
      } else {
        incomplete.observed = value;
        incomplete.expected = value;
      }
      expect(findingCategory(incomplete)).toBe("evidence");
    },
  );
  it.each([
    "rule_source",
    "rule_status",
    "rule_approval",
    "approval",
    "selection_conflict",
    "not_applicable",
  ])("labels %s as rule or authority review", (kind) => {
    expect(findingCategory(finding({ kind }))).toBe("rules");
  });
  it.each([
    "page_coverage",
    "table_coverage",
    "rule_coverage",
    "missing_factor",
    "unknown_factor",
    "unknown_context",
    "inventory",
    "arithmetic_dependency",
    "unresolved",
  ])("keeps %s outside passed or mismatched records", (kind) => {
    expect(findingCategory(finding({ kind }))).toBe("uncovered");
  });
  it("does not infer a mismatch from an unfamiliar failure", () => {
    expect(findingCategory(finding({ kind: "future_check" }))).toBe("evidence");
  });
  it("keeps localized labels and unknown statuses truthful", () => {
    const zh = (_english: string, chinese: string) => chinese;
    expect(categoryLabel("uncovered", zh)).toBe("未覆蓋／未決");
    expect(statusText("succeeded", zh)).toBe("執行成功");
    expect(statusText("future_status", zh)).toBe("future_status");
  });
});
