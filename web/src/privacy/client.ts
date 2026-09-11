import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import contracts from "./contracts.json";
import type { components } from "./schema";
import { LocalOcrReviewClient, OcrReviewRequired, requiredOcrReview } from "./ocr-review-client";
import { responseDiagnostic, transportDiagnostic, type PrivacyDiagnostic } from "./diagnostics";

export type ReviewView = components["schemas"]["PrivacyReviewView"];
export type Selection = components["schemas"]["ReviewSelection"];
export type Region = components["schemas"]["PrivacyRegion"];
export type Category = components["schemas"]["SensitiveCategory"];
export type Version = Pick<
  components["schemas"]["ReviewPrivacyPage"],
  "schema_version" | "case_id" | "snapshot_id" | "revision"
>;
export interface ReviewSnapshot {
  view: ReviewView;
  digest: string;
}
export interface ExportPreview {
  preview_id: string;
  manifest: components["schemas"]["PrivacyManifest"];
  reviewer_text: null;
  payload_digest: string;
}
export interface RestoreResult {
  local_id: string;
  manifest: components["schemas"]["FinalLocalManifest"];
}

const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addFormat("uuid4", /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
ajv.addSchema({ $id: "local-privacy", components: contracts.components });
const validView = ajv.compile({ $ref: "local-privacy#/components/schemas/PrivacyReviewView" });
const validRestoration = ajv.compile({
  $ref: "local-privacy#/components/schemas/FinalLocalManifest",
});
const validManifest = ajv.compile({ $ref: "local-privacy#/components/schemas/PrivacyManifest" });
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DIGEST = /^[a-f0-9]{64}$/;
const id = (value: string) => {
  if (!UUID.test(value)) throw new Error("An opaque identifier is required.");
  return value;
};
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export class BridgeError extends Error {
  constructor(
    readonly unknownOutcome: boolean,
    readonly diagnostic?: PrivacyDiagnostic,
  ) {
    super(
      unknownOutcome
        ? "The local operation's outcome is unknown. Reconcile it before continuing; do not resend a transfer."
        : "The local bridge refused this request. Reload the current review before continuing.",
    );
  }
}

export class LocalPrivacyClient {
  readonly baseUrl: string;
  readonly ocrReviews: LocalOcrReviewClient;
  constructor(
    private readonly options: {
      baseUrl: string;
      token: () => string;
      fetch?: typeof globalThis.fetch;
      timeoutMs?: number;
    },
  ) {
    const base = new URL(options.baseUrl);
    if (
      base.protocol !== "http:" ||
      !["127.0.0.1", "localhost", "[::1]"].includes(base.hostname) ||
      base.username ||
      base.password ||
      base.search ||
      base.hash ||
      base.pathname !== "/"
    )
      throw new Error("Configure a separate loopback HTTP privacy bridge.");
    this.baseUrl = base.origin;
    this.ocrReviews = new LocalOcrReviewClient((path, read, body) =>
      this.request(path, read, body),
    );
  }

  private async request<T>(
    path: string,
    read: (response: Response) => Promise<T>,
    body?: unknown,
    allowOcrReview = false,
  ): Promise<T> {
    const controller = new AbortController();
    // Restoration can render and inspect every page before returning its review stage.
    const duration = this.options.timeoutMs ?? (allowOcrReview ? 120_000 : 30_000);
    const deadline = Date.now() + duration;
    let observed: PrivacyDiagnostic | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new BridgeError(true, transportDiagnostic(observed, "deadline_exceeded")));
      }, duration);
    });
    try {
      const value = await Promise.race([
        (async () => {
          const response = await (this.options.fetch ?? globalThis.fetch)(
            `${this.baseUrl}/local-privacy${path}`,
            {
              method: body === undefined ? "GET" : "POST",
              signal: controller.signal,
              headers: {
                Authorization: `Bearer ${this.options.token()}`,
                ...(body === undefined ? {} : { "Content-Type": "application/json" }),
              },
              ...(body === undefined ? {} : { body: JSON.stringify(body) }),
              cache: "no-store",
              credentials: "omit",
              redirect: "error",
            },
          );
          observed = responseDiagnostic(response.status, response.headers);
          if (!response.ok) {
            if (allowOcrReview && response.status === 409) {
              const required = requiredOcrReview(await response.json());
              if (required) throw required;
            }
            throw new BridgeError(false, observed);
          }
          return await read(response);
        })(),
        timeout,
      ]);
      if (Date.now() >= deadline) {
        controller.abort();
        throw new BridgeError(true, transportDiagnostic(observed, "deadline_exceeded"));
      }
      return value;
    } catch (error) {
      if (error instanceof OcrReviewRequired) throw error;
      if (error instanceof BridgeError && error.diagnostic) throw error;
      throw new BridgeError(
        error instanceof BridgeError ? error.unknownOutcome : true,
        transportDiagnostic(
          observed,
          controller.signal.aborted
            ? "deadline_exceeded"
            : observed
              ? "response_unusable"
              : "request_failed",
        ),
      );
    } finally {
      clearTimeout(timer);
    }
  }

  private review(path: string, body?: unknown): Promise<ReviewSnapshot> {
    return this.request(
      path,
      async (response) => {
        const view: unknown = await response.json();
        const digest = response.headers.get("X-Privacy-Review-Digest");
        if (!validView(view) || digest === null || !DIGEST.test(digest))
          throw new BridgeError(true);
        return { view: view as ReviewView, digest };
      },
      body,
    );
  }

  async sources(): Promise<string[]> {
    return this.request("/sources", async (response) => {
      const value: unknown = await response.json();
      if (!object(value) || !Array.isArray(value.sources)) throw new BridgeError(true);
      return value.sources.map((entry: unknown) => {
        if (!object(entry) || typeof entry.source_id !== "string") throw new BridgeError(true);
        return id(entry.source_id);
      });
    });
  }
  open(sourceId: string) {
    return this.review(`/sources/${id(sourceId)}/open`, {});
  }
  reload() {
    return this.review("/review");
  }
  markPage(version: Version, page: number) {
    return this.review("/review/pages", { ...version, page });
  }
  add(command: components["schemas"]["AddPrivacyRegion"]) {
    return this.review("/review/add", command);
  }
  edit(command: components["schemas"]["EditPrivacyRegion"]) {
    return this.review("/review/edit", command);
  }
  remove(command: components["schemas"]["RemovePrivacyRegion"]) {
    return this.review("/review/remove", command);
  }
  confirmReview(snapshot: ReviewSnapshot) {
    return this.review("/review/confirm", {
      ...versionOf(snapshot),
      review_digest: snapshot.digest,
    });
  }

  private blob(path: string, mediaType: string): Promise<Blob> {
    return this.request(path, async (response) => {
      if (response.headers.get("Content-Type")?.split(";")[0] !== mediaType)
        throw new BridgeError(true);
      return response.blob();
    });
  }
  page(page: number) {
    if (!Number.isInteger(page) || page < 1) throw new Error("Invalid page");
    return this.blob(`/pages/${page}`, "image/png");
  }
  private verifiedPdf(path: string, expectedDigest: string): Promise<Blob> {
    return this.request(path, async (response) => {
      if (response.headers.get("Content-Type")?.split(";")[0] !== "application/pdf")
        throw new BridgeError(true);
      const bytes = await response.arrayBuffer();
      const hash = await crypto.subtle.digest("SHA-256", bytes);
      const digest = [...new Uint8Array(hash)]
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("");
      if (digest !== expectedDigest) throw new BridgeError(true);
      return new Blob([bytes], { type: "application/pdf" });
    });
  }
  previewPdf(preview: ExportPreview) {
    return this.verifiedPdf(
      `/exports/${id(preview.preview_id)}/pdf`,
      preview.manifest.sanitized_digest,
    );
  }
  restoredPdf(result: RestoreResult) {
    return this.verifiedPdf(`/restored/${id(result.local_id)}`, result.manifest.final_digest);
  }

  prepare(): Promise<ExportPreview> {
    return this.request(
      "/exports/preview",
      async (response) => {
        const value: unknown = await response.json();
        if (
          !object(value) ||
          typeof value.preview_id !== "string" ||
          typeof value.payload_digest !== "string" ||
          !DIGEST.test(value.payload_digest) ||
          !validManifest(value.manifest) ||
          value.reviewer_text !== null
        )
          throw new BridgeError(true);
        id(value.preview_id);
        return value as unknown as ExportPreview;
      },
      {},
    );
  }
  confirmExport(preview: ExportPreview): Promise<void> {
    return this.request(
      `/exports/${id(preview.preview_id)}/confirm`,
      async (response) => {
        const value: unknown = await response.json();
        if (
          !object(value) ||
          value.preview_id !== preview.preview_id ||
          value.state !== "confirmed"
        )
          throw new BridgeError(true);
      },
      { payload_digest: preview.payload_digest },
    );
  }
  transfer(previewId: string): Promise<void> {
    return this.request(
      `/exports/${id(previewId)}/transfer`,
      async (response) => {
        const value: unknown = await response.json();
        if (!object(value) || value.state !== "transferred" || !validManifest(value.manifest))
          throw new BridgeError(true);
      },
      {},
    );
  }
  restore(resultId: string): Promise<RestoreResult> {
    return this.request(
      `/restore/${id(resultId)}`,
      async (response) => {
        const value: unknown = await response.json();
        if (
          !object(value) ||
          typeof value.local_id !== "string" ||
          !validRestoration(value.manifest)
        )
          throw new BridgeError(true);
        id(value.local_id);
        return value as unknown as RestoreResult;
      },
      {},
      true,
    );
  }
}

export function versionOf(snapshot: ReviewSnapshot): Version {
  const command = snapshot.view.command;
  return {
    schema_version: "local-privacy-v1",
    case_id: command.source.case_id,
    snapshot_id: command.source.snapshot_id,
    revision: command.selection_revision,
  };
}
