import { validateResponse } from "./validation";
import { canonicalProblem, ServiceError, TransportError, UNKNOWN_OUTCOME } from "./problems";
import type { components } from "./schema";

export type HumanTask = components["schemas"]["HumanTask"];
export type TaskSubjectView = components["schemas"]["TaskSubjectView"];
export type TaskView = components["schemas"]["TaskView"];
export type ValueRevision = components["schemas"]["ValueRevision-Input"];
export type PublicValue = components["schemas"]["PublicValue-Input"];
export type HumanResponse = components["schemas"]["HumanResponse"];
export type TaskListView = components["schemas"]["TaskListView"];
export type RevisionListView = components["schemas"]["RevisionListView"];
export type ResponseReceipt = components["schemas"]["ResponseReceipt"];
export type JobStatusView = components["schemas"]["JobStatusView"];
export type ServiceResult = components["schemas"]["ServiceResult"];
export type MaterialRevision = components["schemas"]["MaterialRevision"];
export type ArtifactManifest = ServiceResult["artifacts"][number];
export type SourceCitation = components["schemas"]["SourceCitation"];

export interface ClientOptions {
  baseUrl: string;
  /**
   * Supplies the bearer token per request. The workbench never stores a token in
   * localStorage and never decodes one to decide what to show: #25 forbids the frontend
   * inferring its own permissions, so authority comes only from what the service answers.
   */
  token: () => Promise<string | null>;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 15_000;

export class ReviewClient {
  private readonly options: ClientOptions;

  constructor(options: ClientOptions) {
    this.options = options;
  }

  async listJobTasks(jobId: string): Promise<TaskListView> {
    return this.request<TaskListView>(
      "TaskListView",
      "GET",
      `/v1/review-jobs/${encode(jobId)}/tasks`,
    );
  }

  async listJobRevisions(jobId: string): Promise<RevisionListView> {
    return this.request<RevisionListView>(
      "RevisionListView",
      "GET",
      `/v1/review-jobs/${encode(jobId)}/revisions`,
    );
  }

  async readJob(jobId: string): Promise<JobStatusView> {
    return this.request<JobStatusView>("JobStatusView", "GET", `/v1/review-jobs/${encode(jobId)}`);
  }

  async readJobResult(jobId: string): Promise<ServiceResult> {
    return this.request<ServiceResult>(
      "ServiceResult",
      "GET",
      `/v1/review-jobs/${encode(jobId)}/result`,
    );
  }

  async readTask(taskId: string): Promise<TaskView> {
    return this.request<TaskView>("TaskView", "GET", `/v1/review-tasks/${encode(taskId)}`);
  }

  async readTaskSubject(taskId: string): Promise<TaskSubjectView> {
    return this.request<TaskSubjectView>(
      "TaskSubjectView",
      "GET",
      `/v1/review-tasks/${encode(taskId)}/subject`,
    );
  }

  /**
   * Submitting is safe to repeat only because the body carries an idempotency key the
   * caller chose once. A retry with the same key returns the first receipt unchanged; a
   * different payload under that key is refused. Never mint a fresh key on retry.
   */
  async submitResponse(taskId: string, command: HumanResponse): Promise<ResponseReceipt> {
    return this.request<ResponseReceipt>(
      "ResponseReceipt",
      "POST",
      `/v1/review-tasks/${encode(taskId)}/responses`,
      command,
    );
  }

  async readSource(citation: SourceCitation): Promise<ArrayBuffer> {
    const query = new URLSearchParams({
      version: citation.version,
      content_hash: citation.content_hash,
    });
    return this.readPdf(
      `/v1/documents/${encode(citation.document_id)}/content?${query}`,
      citation.content_hash,
    );
  }

  async downloadArtifact(jobId: string, artifact: ArtifactManifest): Promise<ArrayBuffer> {
    return this.readPdf(
      `/v1/review-jobs/${encode(jobId)}/artifacts/${encode(artifact.artifact_id)}/content`,
      artifact.content_hash,
    );
  }

  private async readPdf(path: string, expectedHash: string): Promise<ArrayBuffer> {
    return this.transport(async (signal) => {
      const token = await this.options.token();
      const response = await (this.options.fetch ?? globalThis.fetch)(
        `${this.options.baseUrl}${path}`,
        {
          signal,
          headers: {
            Accept: "application/pdf",
            ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
          },
        },
      );
      if (!response.ok) {
        const text = await response.text();
        let problem: unknown = null;
        try {
          problem = JSON.parse(text) as unknown;
        } catch {
          /* Use the HTTP problem fallback. */
        }
        throw canonicalProblem(response.status, problem) ?? new TransportError(UNKNOWN_OUTCOME);
      }
      if (response.headers.get("Content-Type")?.split(";")[0] !== "application/pdf")
        throw new Error("Unexpected content type");
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength > 32 * 1024 * 1024) throw new Error("PDF exceeds client limit");
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const hash = [...new Uint8Array(digest)].map((v) => v.toString(16).padStart(2, "0")).join("");
      if (hash !== expectedHash) throw new Error("PDF identity mismatch");
      return bytes;
    });
  }

  private async request<T>(
    schema: string,
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const doFetch = this.options.fetch ?? globalThis.fetch;
    return this.transport(async (signal) => {
      const token = await this.options.token();
      const response = await doFetch(`${this.options.baseUrl}${path}`, {
        method,
        signal,
        headers: {
          Accept: "application/json",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const text = await response.text();
      let payload: unknown;
      try {
        payload = JSON.parse(text) as unknown;
      } catch (error) {
        if (response.ok) throw error;
        payload = null;
      }
      if (!response.ok) {
        // Only the service's own canonical envelope is a decision. A gateway's HTML is not,
        // and the caller must keep its command and key to recover from it.
        throw canonicalProblem(response.status, payload) ?? new TransportError(UNKNOWN_OUTCOME);
      }
      if (!validateResponse(schema, payload)) throw new Error("Invalid service response");
      return payload as T;
    });
  }

  /** Headers, body, parsing and validation share one deadline and unknown-outcome boundary. */
  private async transport<T>(read: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const controller = new AbortController();
    const duration = this.options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const deadline = Date.now() + duration;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<never>((_resolve, reject) => {
      timeout = setTimeout(() => {
        controller.abort();
        reject(new DOMException("Deadline exceeded", "AbortError"));
      }, duration);
    });
    try {
      const value = await Promise.race([read(controller.signal), expired]);
      // Parsing and synchronous validation can occupy the event loop past the deadline.
      if (Date.now() >= deadline) throw new DOMException("Deadline exceeded", "AbortError");
      return value;
    } catch (cause) {
      if (cause instanceof ServiceError) throw cause;
      // An unknown outcome already described precisely keeps its own wording.
      if (cause instanceof TransportError) throw cause;
      throw new TransportError(
        controller.signal.aborted || (cause instanceof Error && cause.name === "AbortError")
          ? "The service did not answer in time."
          : "The service response could not be read or validated.",
      );
    } finally {
      clearTimeout(timeout);
    }
  }
}

function encode(segment: string): string {
  return encodeURIComponent(segment);
}

/**
 * One key per reviewed command. Every retry carries that same command and key.
 */
export function newIdempotencyKey(random: () => string = () => crypto.randomUUID()): string {
  return `wb-${random()}`;
}
