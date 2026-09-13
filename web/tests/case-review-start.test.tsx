/** Starting a review from a case page: what the service says, and nothing invented. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { CaseReviewBasis, ReviewClient } from "@/api/client";
import { CaseDetail } from "@/features/CaseDetail";
import { LanguageProvider } from "@/ui/Language";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";

function readyBasis(): CaseReviewBasis {
  return {
    case_id: CASE_ID,
    state: "ready",
    reason: null,
    revision: { schema_version: "service-v1", case_id: CASE_ID, revision_id: "rev-1" },
    documents: [{ document_id: "doc-1" }, { document_id: "doc-2" }],
    existing_job_id: null,
  };
}

function client(overrides: Partial<ReviewClient> = {}): ReviewClient {
  return {
    readCase: vi.fn(() =>
      Promise.resolve({
        case_id: CASE_ID,
        title: "樹林區查估案",
        district: "樹林區",
        created_at: 1789230000,
        valuation_date: null,
      }),
    ),
    listCaseMaterials: vi.fn(() => Promise.resolve({ materials: [] })),
    readCaseReviewBasis: vi.fn(() => Promise.resolve(readyBasis())),
    startReview: vi.fn(() => Promise.resolve({ job_id: JOB_ID, job_status: "queued" })),
    ...overrides,
  } as unknown as ReviewClient;
}

function show(service: ReviewClient) {
  return render(
    <LanguageProvider language="zh">
      <MemoryRouter initialEntries={[`/cases/${CASE_ID}`]}>
        <Routes>
          <Route path="/cases/:caseId" element={<CaseDetail client={service} />} />
          <Route path="/jobs/:jobId/progress" element={<p>審查工作台</p>} />
        </Routes>
      </MemoryRouter>
    </LanguageProvider>,
  );
}

afterEach(cleanup);

describe("starting a review from the case page", () => {
  it("offers the button only when the service reports admitted material, and opens the job", async () => {
    const startReview = vi.fn(() => Promise.resolve({ job_id: JOB_ID, job_status: "queued" }));
    const service = client({ startReview });
    const user = userEvent.setup();
    show(service);
    const button = await screen.findByRole("button", { name: /發起審查/ });
    await user.click(button);
    await screen.findByText("審查工作台");
    // The submission pins exactly what the service supplied; the page invents nothing.
    expect(startReview).toHaveBeenCalledWith(
      expect.objectContaining({ case_id: CASE_ID, state: "ready" }),
    );
  });

  it("shows the service's own reason and no button when nothing is admitted", async () => {
    const startReview = vi.fn(() => Promise.reject(new Error("must not be called")));
    const service = client({
      readCaseReviewBasis: vi.fn(() =>
        Promise.resolve({
          case_id: CASE_ID,
          state: "no_material" as const,
          reason: "尚未有受控文件批次。",
          revision: null,
          documents: [],
          existing_job_id: null,
        }),
      ),
      startReview,
    });
    show(service);
    expect(await screen.findByText(/尚未有受控文件批次/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /發起審查/ })).not.toBeInTheDocument();
    expect(startReview).not.toHaveBeenCalled();
  });

  it("keeps the case readable when the basis read itself fails", async () => {
    const service = client({
      readCaseReviewBasis: vi.fn(() => Promise.reject(new Error("offline"))),
    });
    show(service);
    expect(await screen.findByText(/樹林區查估案/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /發起審查/ })).not.toBeInTheDocument();
  });

  it("reports a refused start without claiming a review was created", async () => {
    const { ServiceError } = await import("@/api/problems");
    const service = client({
      startReview: vi.fn(() => Promise.reject(new ServiceError("unauthorized", 403))),
    });
    const user = userEvent.setup();
    show(service);
    await user.click(await screen.findByRole("button", { name: /發起審查/ }));
    await waitFor(() => expect(screen.getByText(/沒有發起審查的權限/)).toBeInTheDocument());
    expect(screen.queryByText("審查工作台")).not.toBeInTheDocument();
  });
});
