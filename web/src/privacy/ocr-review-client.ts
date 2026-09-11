import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import contract from "./ocr-review-contract.json";
import { evidenceBox } from "@/ui/evidenceGeometry";

type Box = [number, number, number, number];
export interface OcrPage {
  number: number;
  width: number;
  height: number;
  image_sha256: string;
}
export interface OcrObservation {
  observation_id: string;
  page: number;
  bbox: Box;
  raw_text: string;
  confidence: number | null;
  review_item_id: string | null;
}
export interface OcrItem {
  item_id: string;
  kind: "observation" | "placeholder";
  page: number;
  bbox: Box;
  observation_ids: string[];
  expected_text: string | null;
  confirmed_reading: string | null;
}
export interface OcrReceipt {
  receipt_id: string;
  review_id: string;
  stage: "published" | "restored";
  principal_id: string;
  binding_digest: string;
  review_digest: string;
  engine_digest: string;
  input_sha256: string;
  measurements_digest: string;
  page_image_sha256: string[];
  readings: OcrItem[];
  confirmed_at: string;
  expires_at: string;
  authority_scope: "local_visual_ocr_readings_only";
  business_authority: "unchanged";
}
export interface OcrReviewView {
  review_id: string;
  stage: "published" | "restored";
  review_digest: string;
  engine_digest: string;
  input_sha256: string;
  pages: OcrPage[];
  observations: OcrObservation[];
  items: OcrItem[];
  receipts: OcrReceipt[];
}

const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addFormat("uuid4", /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
const validView = ajv.compile<OcrReviewView>(contract);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const id = (value: string) => {
  if (!UUID.test(value)) throw new Error("Invalid local review identifier.");
  return value;
};

export class OcrReviewRequired extends Error {
  constructor(readonly reviewId: string) {
    super("Individual local visual OCR review is required.");
    id(reviewId);
  }
}

export function requiredOcrReview(value: unknown): OcrReviewRequired | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  return Object.keys(body).length === 2 &&
    body.code === "local_privacy_review_required" &&
    typeof body.review_id === "string" &&
    UUID.test(body.review_id)
    ? new OcrReviewRequired(body.review_id)
    : null;
}

export function validOcrReading(reading: string): boolean {
  return (
    Boolean(reading.trim()) &&
    reading.length <= 4096 &&
    !Array.from(reading).some((character) => character.charCodeAt(0) < 32)
  );
}

function unique(values: (string | number)[]) {
  return new Set(values).size === values.length;
}

function checkedView(value: unknown, reviewId: string): OcrReviewView {
  if (!validView(value)) throw new Error("Invalid local OCR review response.");
  const view = value;
  const pages = new Map(view.pages.map((page) => [page.number, page]));
  const items = new Map(view.items.map((item) => [item.item_id, item]));
  const observations = new Map(view.observations.map((obs) => [obs.observation_id, obs]));
  if (
    view.review_id !== reviewId ||
    !pages.size ||
    !unique(view.pages.map((page) => page.number)) ||
    !unique(view.items.map((item) => item.item_id)) ||
    !unique(view.observations.map((obs) => obs.observation_id)) ||
    !unique(view.receipts.map((receipt) => receipt.receipt_id))
  )
    throw new Error("Mismatched local OCR review response.");
  for (const entry of [...view.items, ...view.observations]) {
    const page = pages.get(entry.page);
    if (!page || !evidenceBox(entry.bbox, page.width, page.height))
      throw new Error("Invalid local OCR page region.");
  }
  for (const item of view.items) {
    if (
      !unique(item.observation_ids) ||
      item.observation_ids.some((obsId) => {
        const obs = observations.get(obsId);
        return !obs || obs.page !== item.page || obs.review_item_id !== item.item_id;
      })
    )
      throw new Error("Invalid local OCR item observations.");
  }
  for (const obs of view.observations) {
    if (
      obs.review_item_id !== null &&
      !items.get(obs.review_item_id)?.observation_ids.includes(obs.observation_id)
    )
      throw new Error("Missing local OCR review item.");
  }
  return view;
}

function measurements(view: OcrReviewView) {
  return JSON.stringify({
    review_id: view.review_id,
    stage: view.stage,
    review_digest: view.review_digest,
    engine_digest: view.engine_digest,
    input_sha256: view.input_sha256,
    pages: view.pages,
    observations: view.observations,
    items: view.items.map((item) => ({ ...item, confirmed_reading: null })),
  });
}

export function currentOcrReceipt(view: OcrReviewView): OcrReceipt | undefined {
  if (view.items.some((item) => !item.confirmed_reading?.trim())) return undefined;
  return view.receipts.find(
    (receipt) =>
      receipt.review_id === view.review_id &&
      receipt.stage === view.stage &&
      receipt.review_digest === view.review_digest &&
      receipt.engine_digest === view.engine_digest &&
      receipt.input_sha256 === view.input_sha256 &&
      receipt.authority_scope === "local_visual_ocr_readings_only" &&
      receipt.business_authority === "unchanged" &&
      Date.parse(receipt.expires_at) > Date.now() &&
      JSON.stringify(receipt.page_image_sha256) ===
        JSON.stringify(view.pages.map((page) => page.image_sha256)) &&
      receipt.readings.length === view.items.length &&
      unique(receipt.readings.map((item) => item.item_id)) &&
      view.items.every((item) => {
        const reading = receipt.readings.find((entry) => entry.item_id === item.item_id);
        return reading !== undefined && JSON.stringify(reading) === JSON.stringify(item);
      }),
  );
}

type LocalRequest = <T>(
  path: string,
  read: (response: Response) => Promise<T>,
  body?: unknown,
) => Promise<T>;

/** Uses only the existing local bridge's authenticated, bounded transport. */
export class LocalOcrReviewClient {
  private readonly verifiedPages = new Set<string>();
  constructor(private readonly request: LocalRequest) {}

  get(reviewId: string): Promise<OcrReviewView> {
    return this.request(`/restore-reviews/${id(reviewId)}`, async (response) =>
      checkedView(await response.json(), reviewId),
    );
  }

  restart(reviewId: string, resultId: string): Promise<void> {
    id(resultId);
    return this.request(
      `/restore-reviews/${id(reviewId)}/restart`,
      async (response) => {
        const body: unknown = await response.json();
        if (
          body === null ||
          typeof body !== "object" ||
          Array.isArray(body) ||
          Object.keys(body).length !== 2 ||
          !("state" in body) ||
          body.state !== "restarted" ||
          !("result_id" in body) ||
          body.result_id !== resultId
        )
          throw new Error("The local OCR restart did not return the bound result.");
      },
      {},
    );
  }

  async page(view: OcrReviewView, number: number): Promise<Blob> {
    const page = view.pages.find((entry) => entry.number === number);
    if (!page) throw new Error("Unknown local OCR page.");
    return this.request(
      `/restore-reviews/${id(view.review_id)}/pages/${number}`,
      async (response) => {
        if (response.headers.get("Content-Type")?.split(";")[0] !== "image/png")
          throw new Error("Invalid local OCR image.");
        const bytes = await response.arrayBuffer();
        const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
          .map((value) => value.toString(16).padStart(2, "0"))
          .join("");
        if (digest !== page.image_sha256) throw new Error("Local OCR image changed.");
        this.verifiedPages.add(`${view.review_id}:${view.review_digest}:${number}:${digest}`);
        return new Blob([bytes], { type: "image/png" });
      },
    );
  }

  confirm(view: OcrReviewView, itemId: string, reading: string): Promise<OcrReviewView> {
    const item = view.items.find((entry) => entry.item_id === itemId);
    const page = view.pages.find((entry) => entry.number === item?.page);
    if (
      !item ||
      !page ||
      item.confirmed_reading !== null ||
      !validOcrReading(reading) ||
      !this.verifiedPages.has(
        `${view.review_id}:${view.review_digest}:${page.number}:${page.image_sha256}`,
      ) ||
      (item.kind === "placeholder" && reading !== item.expected_text)
    )
      throw new Error("Inspect and transcribe this exact local region before confirming.");
    const original = measurements(view);
    return this.request(
      `/restore-reviews/${id(view.review_id)}/items/${id(itemId)}/confirm`,
      async (response) => {
        const updated = checkedView(await response.json(), view.review_id);
        if (
          measurements(updated) !== original ||
          updated.items.some(
            (entry) =>
              entry.confirmed_reading !==
              (entry.item_id === itemId
                ? reading
                : view.items.find((previous) => previous.item_id === entry.item_id)
                    ?.confirmed_reading),
          )
        )
          throw new Error("Local OCR measurements or confirmations changed. Reload the review.");
        return updated;
      },
      { review_digest: view.review_digest, page_image_sha256: page.image_sha256, reading },
    );
  }
}
