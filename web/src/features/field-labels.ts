/**
 * Readable zh-TW labels for service identifiers (factor ids, candidate field keys and
 * canonical subject names), so main copy can speak the reviewer's language while the raw
 * identifier stays available verbatim inside each panel's 技術紀錄 disclosure.
 *
 * The mapping is presentation only. It never renames the identifier that is sent back to
 * the service, and an unknown key falls back to the raw text rather than being hidden or
 * guessed at: showing "synthetic.road_width" is honest; inventing a Chinese name for an
 * unrecognized factor is not.
 */

type Translate = (english: string, chinese: string) => string;

/** Base vocabulary keyed by the trailing identifier segment, without unit/grade suffixes. */
const BASE_WORDS: Record<string, [string, string]> = {
  road_width: ["Road width", "道路寬度"],
  frontage_road_width: ["Frontage road width", "面前道路寬度"],
  main_road_width: ["Main road width", "主要道路寬度"],
  avg_road_width: ["Average road width", "平均道路寬度"],
  depth: ["Depth", "縱深"],
  road_type: ["Road type", "道路種類"],
  area: ["Area", "面積"],
  market_distance: ["Market distance", "市場距離"],
  consumer_market: ["Consumer market", "消費市場"],
  bus_stop: ["Bus stop", "公車站"],
  bus_stop_access: ["Bus stop access", "公車站便利性"],
  drainage: ["Drainage", "排水"],
  building_coverage: ["Building coverage", "建蔽率"],
  building_density: ["Building density", "容積率"],
  parking: ["Parking", "停車便利"],
  transaction_date: ["Transaction date", "交易日期"],
  effective_date: ["Applicable date", "適用日期"],
};

/** Suffix decorations that follow a base term in mapping-source field names. */
const SUFFIX_WORDS: [string, [string, string]][] = [
  ["_adjustment_pct", ["adjustment %", "修正率"]],
  ["_grade_label", ["grade label", "等級標示"]],
  ["_grade_scale", ["grade scale", "等級級距"]],
  ["_grade", ["grade", "等級"]],
  ["_distance_m", ["distance (m)", "距離（公尺）"]],
  ["_count", ["count", "數量"]],
  ["_name", ["name", "名稱"]],
  ["_pct", ["%", "（％）"]],
  ["_m", ["(m)", "（公尺）"]],
];

const TABLE_WORDS: Record<string, [string, string]> = {
  table_3: ["Form 3", "表3"],
  table_4: ["Form 4", "表4"],
  table_5: ["Form 5", "表5"],
};

/** UUIDs and long digests carry no meaning for a reviewer; keep them for 技術紀錄 only. */
export function looksOpaque(id: string | null | undefined): boolean {
  if (!id) return true;
  return (
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ||
    /^[0-9a-f]{32,}$/i.test(id)
  );
}

/** The trailing meaningful segment of ids like "synthetic.road_width". */
function tailSegment(key: string): string {
  const parts = key.split(".");
  return parts[parts.length - 1] ?? key;
}

function translateTail(tail: string, t: Translate): string | null {
  const direct = BASE_WORDS[tail];
  if (direct) return t(...direct);
  for (const [suffix, words] of SUFFIX_WORDS) {
    if (tail.endsWith(suffix)) {
      const base = BASE_WORDS[tail.slice(0, -suffix.length)];
      if (base) return `${t(...base)}${t(" ", "")}${t(...words)}`.trim();
    }
  }
  return null;
}

/**
 * A factor id like "synthetic.road_width" or "road_width" as reviewer copy.
 * Unknown keys come back verbatim - never hidden, never guessed.
 */
export function factorLabel(factorId: string | null | undefined, t: Translate): string | null {
  if (!factorId) return null;
  return translateTail(tailSegment(factorId), t) ?? factorId;
}

/** True when a readable translation exists (so callers know a raw key is on display). */
export function hasReadableLabel(key: string | null | undefined): boolean {
  return !!key && translateTail(tailSegment(key), () => "x") !== null;
}

/**
 * A candidate field key like "table_5.P002.market_distance" as reviewer copy:
 * 「表5 · P002 · 市場距離」. Unknown shapes come back verbatim.
 */
export function fieldKeyLabel(fieldKey: string, t: Translate): string {
  const parts = fieldKey.split(".");
  if (parts.length >= 3 && parts[0] && TABLE_WORDS[parts[0]]) {
    const table = t(...TABLE_WORDS[parts[0]]!);
    const middle = parts.slice(1, -1).join(".");
    const tail = translateTail(parts[parts.length - 1] ?? "", t) ?? parts[parts.length - 1];
    return middle === "case" ? `${table} · ${tail}` : `${table} · ${middle} · ${tail}`;
  }
  return translateTail(tailSegment(fieldKey), t) ?? fieldKey;
}

export interface SubjectParts {
  scope: string | null;
  targetId: string | null;
  comparableId: string | null;
  factorId: string;
  side: "target" | "comparable" | null;
}

/**
 * Parse the server's canonical subject name, e.g.
 * `["regional","板橋-A","三重-B"]:road_width:target`. Returns null when the shape is not
 * canonical, so callers keep showing the raw name instead of a wrong reading.
 */
export function parseSubjectId(subjectId: string | null | undefined): SubjectParts | null {
  if (!subjectId) return null;
  const closing = subjectId.lastIndexOf("]");
  if (!subjectId.startsWith("[") || closing < 0) return null;
  const rest = subjectId.slice(closing + 1);
  const restMatch = /^:([^:]+):(target|comparable)$/.exec(rest);
  if (!restMatch) return null;
  try {
    const context: unknown = JSON.parse(subjectId.slice(0, closing + 1));
    if (!Array.isArray(context) || context.some((entry) => typeof entry !== "string")) return null;
    const [scope = null, targetId = null, comparableId = null] = context as string[];
    return {
      scope,
      targetId,
      comparableId,
      factorId: restMatch[1] as string,
      side: restMatch[2] as "target" | "comparable",
    };
  } catch {
    return null;
  }
}

export function sideLabel(side: "target" | "comparable" | null | undefined, t: Translate): string {
  if (side === "target") return t("Target side", "基準側");
  if (side === "comparable") return t("Comparable side", "比較側");
  return t("Subject", "標的");
}

/**
 * The whole canonical subject as one readable phrase:
 * 「道路寬度（基準側，板橋-A × 三重-B）」. Falls back to the raw name.
 */
export function subjectLabel(subjectId: string | null | undefined, t: Translate): string | null {
  if (!subjectId) return null;
  const parts = parseSubjectId(subjectId);
  if (!parts) return subjectId;
  const factor = factorLabel(parts.factorId, t) ?? parts.factorId;
  const pair =
    parts.targetId && parts.comparableId ? `，${parts.targetId} × ${parts.comparableId}` : "";
  return `${factor}（${sideLabel(parts.side, t)}${pair}）`;
}
