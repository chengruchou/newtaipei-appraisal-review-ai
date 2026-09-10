import { problemFromResponse, TransportError } from "./problems";
import type { components } from "./schema";

export type HumanTask = components["schemas"]["HumanTask"];
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
    return this.request<TaskListView>("GET", `/v1/review-jobs/${encode(jobId)}/tasks`);
  }

  async listJobRevisions(jobId: string): Promise<RevisionListView> {
    return this.request<RevisionListView>("GET", `/v1/review-jobs/${encode(jobId)}/revisions`);
  }

  async readJob(jobId: string): Promise<JobStatusView> {
    return this.request<JobStatusView>("GET", `/v1/review-jobs/${encode(jobId)}`);
  }

  async readJobResult(jobId: string): Promise<ServiceResult> {
    return this.request<ServiceResult>("GET", `/v1/review-jobs/${encode(jobId)}/result`);
  }

  async readTask(taskId: string): Promise<TaskView> {
    return this.request<TaskView>("GET", `/v1/review-tasks/${encode(taskId)}`);
  }

  /**
   * Submitting is safe to repeat only because the body carries an idempotency key the
   * caller chose once. A retry with the same key returns the first receipt unchanged; a
   * different payload under that key is refused. Never mint a fresh key on retry.
   */
  async submitResponse(taskId: string, command: HumanResponse): Promise<ResponseReceipt> {
    return this.request<ResponseReceipt>(
      "POST",
      `/v1/review-tasks/${encode(taskId)}/responses`,
      command,
    );
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const doFetch = this.options.fetch ?? globalThis.fetch;
    const token = await this.options.token();
    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      this.options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    );
    let response: Response;
    try {
      response = await doFetch(`${this.options.baseUrl}${path}`, {
        method,
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (cause) {
      // A timeout or a dropped connection leaves the outcome genuinely unknown, which is
      // exactly the case the idempotency key exists for. Say so rather than guessing.
      throw new TransportError(
        cause instanceof Error && cause.name === "AbortError"
          ? "The service did not answer in time."
          : "The service could not be reached.",
      );
    } finally {
      clearTimeout(timeout);
    }
    const payload = await readJson(response);
    if (!response.ok) {
      throw problemFromResponse(response.status, payload);
    }
    return payload as T;
  }
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text === "") {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function encode(segment: string): string {
  return encodeURIComponent(segment);
}

/**
 * One key per logical submission, minted when the reviewer opens the form rather than
 * when they press submit, so every retry of that one decision carries the same key.
 */
export function newIdempotencyKey(random: () => string = () => crypto.randomUUID()): string {
  return `wb-${random()}`;
}
