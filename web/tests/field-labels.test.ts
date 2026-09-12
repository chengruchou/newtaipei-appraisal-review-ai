/** Pure-label unit regression: readable copy never renames or hides a raw identifier. */
import { describe, expect, it } from "vitest";
import {
  factorLabel,
  fieldKeyLabel,
  hasReadableLabel,
  looksOpaque,
  parseSubjectId,
  sideLabel,
  subjectLabel,
} from "@/features/field-labels";

const zh = (_english: string, chinese: string) => chinese;
const en = (english: string) => english;

describe("factorLabel", () => {
  it("maps known factor ids, with or without a namespace prefix", () => {
    expect(factorLabel("road_width", zh)).toBe("道路寬度");
    expect(factorLabel("synthetic.road_width", zh)).toBe("道路寬度");
    expect(factorLabel("road_width", en)).toBe("Road width");
  });

  it("falls back to the raw id verbatim for unknown factors", () => {
    expect(factorLabel("unit-factor-without-evidence", zh)).toBe("unit-factor-without-evidence");
    expect(hasReadableLabel("unit-factor-without-evidence")).toBe(false);
    expect(hasReadableLabel("synthetic.road_width")).toBe(true);
  });

  it("returns null only for an absent id", () => {
    expect(factorLabel(null, zh)).toBeNull();
    expect(factorLabel(undefined, zh)).toBeNull();
  });
});

describe("fieldKeyLabel", () => {
  it("reads mapping-source keys as 表 · 標的 · 欄位", () => {
    expect(fieldKeyLabel("table_5.P002.market_distance", zh)).toBe("表5 · P002 · 市場距離");
    expect(fieldKeyLabel("table_3.case.avg_road_width_m", zh)).toBe("表3 · 平均道路寬度（公尺）");
  });

  it("keeps unknown shapes verbatim", () => {
    expect(fieldKeyLabel("weird_key", zh)).toBe("weird_key");
    expect(fieldKeyLabel("table_9.x.y", zh)).toBe("table_9.x.y");
  });
});

describe("subject names", () => {
  const SUBJECT = '["regional","板橋-A","三重-B"]:road_width:target';

  it("parses the canonical escaped subject name", () => {
    expect(parseSubjectId(SUBJECT)).toEqual({
      scope: "regional",
      targetId: "板橋-A",
      comparableId: "三重-B",
      factorId: "road_width",
      side: "target",
    });
  });

  it("renders one readable phrase and keeps the raw name for non-canonical ids", () => {
    expect(subjectLabel(SUBJECT, zh)).toBe("道路寬度（基準側，板橋-A × 三重-B）");
    expect(subjectLabel("free-form-subject", zh)).toBe("free-form-subject");
    expect(parseSubjectId("free-form-subject")).toBeNull();
    expect(parseSubjectId('["a","b","c"]:factor:sideways')).toBeNull();
  });

  it("labels sides in both languages", () => {
    expect(sideLabel("target", zh)).toBe("基準側");
    expect(sideLabel("comparable", zh)).toBe("比較側");
    expect(sideLabel(null, en)).toBe("Subject");
  });
});

describe("looksOpaque", () => {
  it("treats UUIDs and digests as opaque, and human-entered ids as readable", () => {
    expect(looksOpaque("11111111-1111-1111-1111-111111111111")).toBe(true);
    expect(looksOpaque("a".repeat(64))).toBe(true);
    expect(looksOpaque("case-1")).toBe(false);
    expect(looksOpaque("板橋-A")).toBe(false);
    expect(looksOpaque(null)).toBe(true);
  });
});
