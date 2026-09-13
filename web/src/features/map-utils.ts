import type { FactCandidate } from "./candidate-api";

/**
 * 地圖核對 helpers: coordinate extraction and a DISPLAY-ONLY straight-line distance.
 *
 * The extractor never invents coordinates. It only reads what the candidate's own
 * evidence actually carries (a structured `evidence.geometry` block if the service ever
 * sends one, or a JSON row inside `evidence.excerpt`, e.g. an NTPC open-data row with
 * `latitude`/`longitude` strings). Anything unparseable or out of range is treated as
 * absent, and the panel then says honestly that only text verification is possible.
 *
 * The haversine distance is for on-map display only. The authoritative number remains
 * the candidate's stored value and unit; callers must never replace that value with the
 * display calculation.
 */

export interface GeoPoint {
  lat: number;
  lng: number;
}

/** Whatever points the evidence actually carried; at least one is non-null. */
export interface CandidateGeo {
  subject: GeoPoint | null;
  facility: GeoPoint | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A finite number, or a string that is entirely one number. Anything else is absent. */
function parseCoordinate(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed || !/^[+-]?\d+(?:\.\d+)?$/.test(trimmed)) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

const LAT_KEYS = ["lat", "latitude"] as const;
const LNG_KEYS = ["lng", "lon", "long", "longitude"] as const;

function firstCoordinate(row: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    // Tolerate NTPC-style header casing (latitude / Latitude / LATITUDE).
    for (const candidateKey of Object.keys(row)) {
      if (candidateKey.toLowerCase() !== key) continue;
      const parsed = parseCoordinate(row[candidateKey]);
      if (parsed !== null) return parsed;
    }
  }
  return null;
}

function boundedPoint(lat: number | null, lng: number | null): GeoPoint | null {
  if (lat === null || lng === null) return null;
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
  return { lat, lng };
}

/** A {lat,lng} / {latitude,longitude} shaped record, else null. */
function parsePoint(value: unknown): GeoPoint | null {
  if (!isRecord(value)) return null;
  return boundedPoint(firstCoordinate(value, LAT_KEYS), firstCoordinate(value, LNG_KEYS));
}

/** subject_lat / subject_latitude … style flat keys. */
function parsePrefixedPoint(row: Record<string, unknown>, prefix: string): GeoPoint | null {
  const stripped: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(row)) {
    const lower = key.toLowerCase();
    if (lower.startsWith(`${prefix}_`)) stripped[lower.slice(prefix.length + 1)] = value;
  }
  return parsePoint(stripped);
}

function pointsFromRow(row: unknown): CandidateGeo {
  if (!isRecord(row)) return { subject: null, facility: null };
  const subject = parsePoint(row.subject) ?? parsePrefixedPoint(row, "subject");
  // A flat row (e.g. one NTPC bus-stop record) describes the facility itself.
  const facility =
    parsePoint(row.facility) ?? parsePrefixedPoint(row, "facility") ?? parsePoint(row);
  return { subject, facility };
}

/**
 * Read whatever coordinates the candidate's evidence actually carries. Returns null when
 * no point is parseable — the caller must then offer text verification only, never a map.
 */
export function extractCandidateGeo(candidate: FactCandidate): CandidateGeo | null {
  let subject: GeoPoint | null = null;
  let facility: GeoPoint | null = null;

  // Optional structured block. The typed DTO does not declare it, so read defensively.
  const geometry = (candidate.evidence as unknown as Record<string, unknown>).geometry;
  if (isRecord(geometry)) {
    const found = pointsFromRow(geometry);
    subject = found.subject;
    facility = found.facility;
  }

  // The excerpt may hold the fetched row as JSON (NTPC open-data rows arrive this way).
  if ((subject === null || facility === null) && candidate.evidence.excerpt) {
    let row: unknown = null;
    try {
      row = JSON.parse(candidate.evidence.excerpt);
    } catch {
      row = null; // Prose excerpts are normal; they simply carry no coordinates.
    }
    const found = pointsFromRow(row);
    subject = subject ?? found.subject;
    facility = facility ?? found.facility;
  }

  if (subject === null && facility === null) return null;
  return { subject, facility };
}

/** Mean earth radius in meters (IUGG). */
const EARTH_RADIUS_M = 6371000;

/** Great-circle (haversine) distance in meters. DISPLAY ONLY; label the method. */
export function haversineMeters(a: GeoPoint, b: GeoPoint): number {
  const toRad = (degrees: number) => (degrees * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const sinLat = Math.sin(dLat / 2);
  const sinLng = Math.sin(dLng / 2);
  const h = sinLat * sinLat + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * sinLng * sinLng;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

const METER_UNITS = new Set(["m", "公尺", "米", "meter", "meters", "metre", "metres"]);
const KILOMETER_UNITS = new Set([
  "km",
  "公里",
  "kilometer",
  "kilometers",
  "kilometre",
  "kilometres",
]);

/**
 * The candidate's stored value expressed in meters, when its unit is a known length
 * unit and its value is one plain number. Null means "cannot compare" — never guess.
 */
export function candidateValueMeters(value: string, unit: string | null): number | null {
  if (unit === null) return null;
  const normalized = unit.trim().toLowerCase();
  const parsed = parseCoordinate(value);
  if (parsed === null || parsed <= 0) return null;
  if (METER_UNITS.has(normalized)) return parsed;
  if (KILOMETER_UNITS.has(normalized)) return parsed * 1000;
  return null;
}

/**
 * True when the display (haversine) distance and the candidate's own stored distance
 * differ by more than 5%, in which case the panel must ask for manual confirmation.
 * When the stored value is not comparable (unknown unit, non-numeric), returns false:
 * there is nothing to contradict, and the stored value stays authoritative regardless.
 */
export function distanceMismatch(
  candidateValue: string,
  unit: string | null,
  displayMeters: number,
): boolean {
  const authoritative = candidateValueMeters(candidateValue, unit);
  if (authoritative === null) return false;
  return Math.abs(displayMeters - authoritative) / authoritative > 0.05;
}
