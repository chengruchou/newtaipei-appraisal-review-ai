/**
 * 地圖核對 unit regressions with a mocked leaflet: coordinate extraction, the
 * display-only haversine, the >5% mismatch warning, and the honest no-map message.
 * Not real-tile or real-backend acceptance.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { CandidatePanel } from "@/features/CandidatePanel";
import { MapVerify } from "@/features/MapVerify";
import {
  candidateValueMeters,
  distanceMismatch,
  extractCandidateGeo,
  haversineMeters,
} from "@/features/map-utils";
import type { CandidateApi, FactCandidate } from "@/features/candidate-api";
import { LanguageProvider } from "@/ui/Language";

const leaflet = vi.hoisted(() => {
  interface MarkerCall {
    latlng: [number, number];
    tooltip: string | null;
  }
  interface PolylineCall {
    latlngs: [number, number][];
    tooltip: string | null;
  }
  const calls = {
    maps: 0,
    removed: 0,
    tileLayers: [] as { url: string; options: Record<string, unknown> }[],
    markers: [] as MarkerCall[],
    polylines: [] as PolylineCall[],
    fitBounds: 0,
    setViews: [] as [number, number][],
  };
  function chain(record: { tooltip: string | null }) {
    const self = {
      addTo: () => self,
      bindTooltip: (text: string) => {
        record.tooltip = text;
        return self;
      },
    };
    return self;
  }
  const L = {
    map: () => {
      calls.maps += 1;
      return {
        fitBounds: () => {
          calls.fitBounds += 1;
        },
        setView: (latlng: [number, number]) => {
          calls.setViews.push(latlng);
        },
        remove: () => {
          calls.removed += 1;
        },
      };
    },
    tileLayer: (url: string, options: Record<string, unknown>) => {
      calls.tileLayers.push({ url, options });
      return chain({ tooltip: null });
    },
    circleMarker: (latlng: [number, number]) => {
      const record: MarkerCall = { latlng, tooltip: null };
      calls.markers.push(record);
      return chain(record);
    },
    polyline: (latlngs: [number, number][]) => {
      const record: PolylineCall = { latlngs, tooltip: null };
      calls.polylines.push(record);
      return chain(record);
    },
    latLngBounds: () => ({ pad: () => ({}) }),
  };
  const reset = () => {
    calls.maps = 0;
    calls.removed = 0;
    calls.tileLayers.length = 0;
    calls.markers.length = 0;
    calls.polylines.length = 0;
    calls.fitBounds = 0;
    calls.setViews.length = 0;
  };
  return { calls, L, reset };
});

vi.mock("leaflet", () => ({ default: leaflet.L }));
vi.mock("leaflet/dist/leaflet.css", () => ({}));

beforeEach(() => {
  leaflet.reset();
});

const SHA = "e".repeat(64);

function candidate(overrides: Partial<FactCandidate> = {}): FactCandidate {
  return {
    candidate_id: "cand-1",
    case_id: "case-1",
    revision_id: "rev-9",
    subject_id: '["regional","板橋-A","三重-B"]:road_width:target',
    field_key: "table_5.P002.market_distance",
    value: "850",
    unit: "m",
    applicable_date: "2026-07-01",
    source_id: "ntpc-open-data",
    evidence: {
      url: "https://data.ntpc.gov.tw/datasets/123",
      sha256: SHA,
      retrieved_at: 1_757_500_000,
      excerpt: "市場距離 850 公尺",
      row_locator: "row-123",
    },
    status: "candidate",
    created_by: { actor_id: "fetcher-1", kind: "system" },
    created_at: 1_757_500_100,
    ...overrides,
  };
}

function withExcerpt(excerpt: string, overrides: Partial<FactCandidate> = {}): FactCandidate {
  const base = candidate(overrides);
  return { ...base, evidence: { ...base.evidence, excerpt } };
}

/** Subject 板橋-ish, facility exactly 0.01° north: haversine 1111.95 m. */
const BOTH_POINTS_ROW = JSON.stringify({
  stop_name: "文化路一段",
  latitude: "25.0239",
  longitude: "121.4627",
  subject_latitude: "25.0139",
  subject_longitude: "121.4627",
});

function api(overrides: Partial<CandidateApi> = {}): CandidateApi {
  return {
    listCandidates: vi.fn(() => Promise.reject(new Error("listCandidates not scripted"))),
    confirmCandidate: vi.fn(() => Promise.reject(new Error("confirmCandidate not scripted"))),
    ...overrides,
  };
}

function zh(ui: ReactNode) {
  return render(<LanguageProvider language="zh">{ui}</LanguageProvider>);
}

function showPanel(rows: FactCandidate[]) {
  return zh(
    <CandidatePanel
      api={api({
        listCandidates: vi.fn(() => Promise.resolve({ case_id: "case-1", candidates: rows })),
      })}
      caseId="case-1"
    />,
  );
}

describe("coordinate extraction", () => {
  it("reads an NTPC-style flat row (string coordinates) as the facility point", () => {
    const geo = extractCandidateGeo(
      withExcerpt(JSON.stringify({ stop_name: "站牌", latitude: "25.10", longitude: "121.64" })),
    );
    expect(geo).toEqual({ subject: null, facility: { lat: 25.1, lng: 121.64 } });
  });

  it("reads nested subject/facility pairs in either lat/lng spelling", () => {
    const geo = extractCandidateGeo(
      withExcerpt(
        JSON.stringify({
          subject: { lat: 25.0139, lng: 121.4627 },
          facility: { latitude: "25.0239", longitude: "121.4627" },
        }),
      ),
    );
    expect(geo).toEqual({
      subject: { lat: 25.0139, lng: 121.4627 },
      facility: { lat: 25.0239, lng: 121.4627 },
    });
  });

  it("reads subject_-prefixed flat keys next to the facility's own row", () => {
    const geo = extractCandidateGeo(withExcerpt(BOTH_POINTS_ROW));
    expect(geo).toEqual({
      subject: { lat: 25.0139, lng: 121.4627 },
      facility: { lat: 25.0239, lng: 121.4627 },
    });
  });

  it("accepts a structured evidence.geometry block when the service sends one", () => {
    const base = candidate();
    const evidence = {
      ...base.evidence,
      geometry: {
        subject: { latitude: 25.0139, longitude: 121.4627 },
        facility: { lat: "25.0239", lng: "121.4627" },
      },
    } as unknown as FactCandidate["evidence"];
    expect(extractCandidateGeo({ ...base, evidence })).toEqual({
      subject: { lat: 25.0139, lng: 121.4627 },
      facility: { lat: 25.0239, lng: 121.4627 },
    });
  });

  it.each([
    ["prose excerpt", "市場距離 850 公尺"],
    ["malformed JSON", '{"latitude": 25.1,'],
    ["empty excerpt", ""],
    ["latitude out of range", JSON.stringify({ latitude: "95.0", longitude: "121.64" })],
    ["longitude out of range", JSON.stringify({ latitude: "25.1", longitude: "200" })],
    ["non-numeric strings", JSON.stringify({ latitude: "25,10", longitude: "121.64" })],
    ["missing longitude", JSON.stringify({ latitude: "25.10" })],
    ["null coordinates", JSON.stringify({ latitude: null, longitude: null })],
  ])("never invents coordinates: %s yields no geo", (_name, excerpt) => {
    expect(extractCandidateGeo(withExcerpt(excerpt))).toBeNull();
  });
});

describe("display-only haversine", () => {
  it("matches the closed form for 0.01 degrees of latitude (1111.95 m) within 1%", () => {
    const d = haversineMeters({ lat: 25, lng: 121.5 }, { lat: 25.01, lng: 121.5 });
    expect(Math.abs(d - 1111.95) / 1111.95).toBeLessThan(0.01);
  });

  it("matches one degree of longitude on the equator (111194.9 m) within 1%", () => {
    const d = haversineMeters({ lat: 0, lng: 0 }, { lat: 0, lng: 1 });
    expect(Math.abs(d - 111194.9) / 111194.9).toBeLessThan(0.01);
  });

  it("puts Taipei Main and Banqiao stations about 6.65 km apart, within 1%", () => {
    const d = haversineMeters(
      { lat: 25.047924, lng: 121.517081 },
      { lat: 25.014524, lng: 121.462359 },
    );
    expect(Math.abs(d - 6647.5) / 6647.5).toBeLessThan(0.01);
  });

  it("is zero for the same point", () => {
    expect(haversineMeters({ lat: 25.1, lng: 121.6 }, { lat: 25.1, lng: 121.6 })).toBe(0);
  });
});

describe("mismatch warning logic", () => {
  it("converts only known length units, never guessing others", () => {
    expect(candidateValueMeters("850", "m")).toBe(850);
    expect(candidateValueMeters("850", "公尺")).toBe(850);
    expect(candidateValueMeters("0.85", "km")).toBe(850);
    expect(candidateValueMeters("1.2", "公里")).toBe(1200);
    expect(candidateValueMeters("850", null)).toBeNull();
    expect(candidateValueMeters("850", "坪")).toBeNull();
    expect(candidateValueMeters("abc", "m")).toBeNull();
    expect(candidateValueMeters("-5", "m")).toBeNull();
  });

  it("flags only differences beyond 5% of the candidate's own value", () => {
    expect(distanceMismatch("1000", "m", 1040)).toBe(false);
    expect(distanceMismatch("1000", "m", 1050)).toBe(false);
    expect(distanceMismatch("1000", "m", 1051)).toBe(true);
    expect(distanceMismatch("1000", "m", 949)).toBe(true);
    expect(distanceMismatch("1", "km", 1040)).toBe(false);
    // Incomparable stored values produce no warning; the stored value simply stands.
    expect(distanceMismatch("850", "坪", 99999)).toBe(false);
    expect(distanceMismatch("850", null, 99999)).toBe(false);
  });
});

describe("candidate cards and the map expander", () => {
  it("shows the honest text-only note and no map button without parseable coordinates", async () => {
    showPanel([candidate()]);
    await screen.findByText("表5 · P002 · 市場距離");
    expect(screen.getByText(/無座標資料，僅能以文字核對/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "地圖核對" })).not.toBeInTheDocument();
    expect(leaflet.calls.maps).toBe(0);
  });

  it("opens a read-only OSM map with both labeled points and the haversine-labeled line", async () => {
    const user = userEvent.setup();
    showPanel([withExcerpt(BOTH_POINTS_ROW, { value: "1112", unit: "m" })]);
    await user.click(await screen.findByRole("button", { name: "地圖核對" }));
    await screen.findByText("直線距離 1112 公尺（haversine，非步行路徑）");

    await waitFor(() => expect(leaflet.calls.maps).toBe(1));
    const tiles = leaflet.calls.tileLayers[0];
    expect(tiles?.url).toBe("https://tile.openstreetmap.org/{z}/{x}/{y}.png");
    expect(tiles?.options.maxZoom).toBe(19);
    expect(String(tiles?.options.attribution)).toContain("OpenStreetMap");

    expect(leaflet.calls.markers).toHaveLength(2);
    expect(leaflet.calls.markers[0]).toEqual({ latlng: [25.0139, 121.4627], tooltip: "標的" });
    expect(leaflet.calls.markers[1]).toEqual({
      latlng: [25.0239, 121.4627],
      tooltip: "ntpc-open-data",
    });
    expect(leaflet.calls.polylines).toHaveLength(1);
    expect(leaflet.calls.polylines[0]?.tooltip).toBe("直線距離 1112 公尺（haversine，非步行路徑）");
    expect(leaflet.calls.fitBounds).toBe(1);

    // Display matches the stored value here, so no mismatch warning.
    expect(screen.queryByText(/顯示距離與候選值不一致/)).not.toBeInTheDocument();
    // The map adds no decision controls of its own; the card's actions stay singular.
    expect(screen.getAllByRole("button", { name: "確認採用" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "拒絕" })).toHaveLength(1);
  });

  it("keeps the stored value authoritative and warns when the display differs by >5%", async () => {
    const user = userEvent.setup();
    showPanel([withExcerpt(BOTH_POINTS_ROW)]); // stored 850 m vs displayed 1112 m
    await user.click(await screen.findByRole("button", { name: "地圖核對" }));
    await screen.findByText("顯示距離與候選值不一致，請人工確認。");
    // The candidate's own number is still shown as the authoritative value.
    expect(screen.getByText("候選值（以服務儲存值為準）")).toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the 地圖核對 button", async () => {
    const user = userEvent.setup();
    showPanel([withExcerpt(BOTH_POINTS_ROW, { value: "1112", unit: "m" })]);
    const toggle = await screen.findByRole("button", { name: "地圖核對" });
    await user.click(toggle);
    await screen.findByRole("button", { name: "關閉地圖" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: "關閉地圖" })).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
    await waitFor(() => expect(leaflet.calls.removed).toBe(1));

    // The close button does the same and also hands focus back.
    await user.click(toggle);
    await user.click(await screen.findByRole("button", { name: "關閉地圖" }));
    expect(screen.queryByRole("button", { name: "關閉地圖" })).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
  });

  it("shows a single point without any distance line when only the facility is known", async () => {
    const user = userEvent.setup();
    showPanel([withExcerpt(JSON.stringify({ latitude: "25.10", longitude: "121.64" }))]);
    await user.click(await screen.findByRole("button", { name: "地圖核對" }));
    await screen.findByText("僅取得單一座標點，無法顯示距離線。");
    await waitFor(() => expect(leaflet.calls.markers).toHaveLength(1));
    expect(leaflet.calls.polylines).toHaveLength(0);
    expect(leaflet.calls.setViews).toEqual([[25.1, 121.64]]);
    expect(screen.queryByText(/顯示距離與候選值不一致/)).not.toBeInTheDocument();
  });
});

describe("MapVerify smoke render", () => {
  it("builds the mocked leaflet map directly from a candidate and its geo", async () => {
    const row = withExcerpt(BOTH_POINTS_ROW, { value: "1112", unit: "m" });
    const geo = extractCandidateGeo(row);
    expect(geo).not.toBeNull();
    if (geo === null) return;
    zh(<MapVerify candidate={row} geo={geo} />);
    await waitFor(() => expect(leaflet.calls.maps).toBe(1));
    expect(leaflet.calls.markers).toHaveLength(2);
    expect(leaflet.calls.polylines).toHaveLength(1);
    expect(screen.getByText(/地圖僅供檢視核對（唯讀）/)).toBeVisible();
    expect(screen.getByText("直線距離 1112 公尺（haversine，非步行路徑）")).toBeVisible();
  });
});
