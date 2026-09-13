import { useEffect, useMemo, useRef, useState } from "react";
import type * as Leaflet from "leaflet";
import { useText } from "@/ui/Language";
import type { FactCandidate } from "./candidate-api";
import {
  distanceMismatch,
  extractCandidateGeo,
  haversineMeters,
  type CandidateGeo,
} from "./map-utils";

/**
 * 地圖核對: a READ-ONLY map so the reviewer can see the subject point, the facility
 * point and a straight measurement line before using the panel's EXISTING confirm and
 * reject actions. The map decides nothing and edits nothing — the distance drawn here is
 * a haversine straight line for display only, clearly labeled as such, and the
 * candidate's stored value with its unit remains the authoritative number.
 *
 * Leaflet is imported dynamically so reviewers who never open a map never download it.
 */

type LeafletModule = typeof Leaflet;

const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

async function loadLeaflet(): Promise<LeafletModule> {
  // The stylesheet is guarded: vitest stubs CSS (css:false) and a styling failure must
  // not block the verification content itself.
  try {
    await import("leaflet/dist/leaflet.css");
  } catch {
    /* Styling only; the map still renders unstyled rather than not at all. */
  }
  const imported: unknown = await import("leaflet");
  const withDefault = imported as { default?: LeafletModule };
  return withDefault.default ?? (imported as LeafletModule);
}

function formatServerSeconds(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    hour12: false,
  });
}

function buildMap(
  L: LeafletModule,
  container: HTMLElement,
  geo: CandidateGeo,
  facilityLabel: string,
  subjectLabel: string,
  distanceText: string | null,
): Leaflet.Map {
  const map = L.map(container, { zoomControl: true });
  L.tileLayer(OSM_TILE_URL, { maxZoom: 19, attribution: OSM_ATTRIBUTION }).addTo(map);
  const points: [number, number][] = [];
  if (geo.subject) {
    const at: [number, number] = [geo.subject.lat, geo.subject.lng];
    points.push(at);
    L.circleMarker(at, { radius: 8, color: "#1d4ed8", weight: 2, fillOpacity: 0.85 })
      .bindTooltip(subjectLabel, { permanent: true, direction: "top" })
      .addTo(map);
  }
  if (geo.facility) {
    const at: [number, number] = [geo.facility.lat, geo.facility.lng];
    points.push(at);
    L.circleMarker(at, { radius: 8, color: "#b45309", weight: 2, fillOpacity: 0.85 })
      .bindTooltip(facilityLabel, { permanent: true, direction: "top" })
      .addTo(map);
  }
  const [first, second] = points;
  if (first && second && distanceText) {
    L.polyline([first, second], { color: "#334155", weight: 3, dashArray: "6 6" })
      .bindTooltip(distanceText, { permanent: true, direction: "center" })
      .addTo(map);
    map.fitBounds(L.latLngBounds(points).pad(0.25));
  } else if (first) {
    map.setView(first, 16);
  }
  return map;
}

export function MapVerify({ candidate, geo }: { candidate: FactCandidate; geo: CandidateGeo }) {
  const t = useText();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "failed">("loading");

  const displayMeters = useMemo(
    () => (geo.subject && geo.facility ? haversineMeters(geo.subject, geo.facility) : null),
    [geo],
  );
  const distanceText =
    displayMeters === null
      ? null
      : t(
          `Straight-line distance ${Math.round(displayMeters)} m (haversine, not a walking route)`,
          `直線距離 ${Math.round(displayMeters)} 公尺（haversine，非步行路徑）`,
        );
  const mismatch =
    displayMeters !== null && distanceMismatch(candidate.value, candidate.unit, displayMeters);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let cancelled = false;
    let map: Leaflet.Map | null = null;
    setPhase("loading");
    loadLeaflet()
      .then((L) => {
        if (cancelled) return;
        map = buildMap(L, container, geo, candidate.source_id, t("Subject", "標的"), distanceText);
        setPhase("ready");
      })
      .catch(() => {
        if (!cancelled) setPhase("failed");
      });
    return () => {
      cancelled = true;
      if (map) map.remove();
    };
  }, [geo, candidate.source_id, distanceText, t]);

  return (
    <div>
      <p className="small muted" style={{ margin: "0.5rem 0 0.25rem" }}>
        {t(
          "The map is read-only and shows a straight line for checking only. Use the existing accept/reject actions above to record a decision.",
          "地圖僅供檢視核對（唯讀）；確認採用或拒絕請使用上方原有操作。",
        )}
      </p>
      <div
        ref={containerRef}
        style={{ height: 320, width: "100%" }}
        aria-label={t("Verification map", "核對地圖")}
      />
      {phase === "loading" ? (
        <p role="status" className="muted">
          {t("Loading map…", "地圖載入中…")}
        </p>
      ) : null}
      {phase === "failed" ? (
        <div className="notice" data-tone="warn" role="alert">
          <p style={{ margin: 0 }}>
            {t(
              "The map component could not be loaded. Verify against the text record instead.",
              "無法載入地圖元件，請改以文字紀錄核對。",
            )}
          </p>
        </div>
      ) : null}
      <dl className="kv">
        <dt>{t("Candidate value (authoritative)", "候選值（以服務儲存值為準）")}</dt>
        <dd>
          <strong>{candidate.value}</strong>
          {candidate.unit ? ` ${candidate.unit}` : ""}
        </dd>
        {distanceText ? (
          <>
            <dt>{t("Displayed distance", "顯示距離")}</dt>
            <dd>{distanceText}</dd>
          </>
        ) : (
          <>
            <dt>{t("Displayed distance", "顯示距離")}</dt>
            <dd>
              {t(
                "Only one coordinate point is available, so no distance line can be shown.",
                "僅取得單一座標點，無法顯示距離線。",
              )}
            </dd>
          </>
        )}
        <dt>{t("Source", "來源")}</dt>
        <dd>{candidate.source_id}</dd>
        <dt>{t("Retrieved at", "資料取得時間")}</dt>
        <dd>{formatServerSeconds(candidate.evidence.retrieved_at)} · Asia/Taipei</dd>
        <dt>{t("Applicable date", "適用日期")}</dt>
        <dd>{candidate.applicable_date ?? t("Not supplied", "未提供")}</dd>
      </dl>
      {mismatch ? (
        <div className="notice" data-tone="warn" role="alert">
          <p style={{ margin: 0 }}>
            {t(
              "The displayed distance does not match the candidate value. Confirm manually.",
              "顯示距離與候選值不一致，請人工確認。",
            )}
          </p>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The per-card entry point: a 地圖核對 button when the evidence carries coordinates, an
 * honest text-only note when it does not. Escape closes the map and focus returns to
 * the button. Confirm/reject stay the card's existing actions.
 */
export function MapVerifySection({ candidate }: { candidate: FactCandidate }) {
  const t = useText();
  const geo = useMemo(() => extractCandidateGeo(candidate), [candidate]);
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement | null>(null);

  if (geo === null) {
    return (
      <p className="small muted">
        {t(
          "No coordinate data; this candidate can only be checked against the text.",
          "無座標資料，僅能以文字核對。",
        )}
      </p>
    );
  }

  const close = () => {
    setOpen(false);
    toggleRef.current?.focus();
  };

  return (
    <div
      onKeyDown={(event) => {
        if (open && event.key === "Escape") {
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        ref={toggleRef}
        type="button"
        aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}
      >
        {t("Map check", "地圖核對")}
      </button>
      {open ? (
        <section aria-label={t("Map check", "地圖核對")}>
          <MapVerify candidate={candidate} geo={geo} />
          <button type="button" onClick={close}>
            {t("Close map", "關閉地圖")}
          </button>
        </section>
      ) : null}
    </div>
  );
}
