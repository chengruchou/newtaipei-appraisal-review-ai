import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { ReviewClient, ServiceResult } from "@/api/client";
import { validateResponse } from "@/api/validation";
import { ResultPanel } from "@/features/ResultPanel";
import { view } from "./fixtures";

it("shows every comparison context in a fenced artifact, not just its primary context", async () => {
  const primary = {
    scope: "regional",
    target_id: "target-A",
    comparable_id: "comparable-B",
  } as const;
  const secondary = {
    scope: "individual",
    target_id: "target-C",
    comparable_id: "comparable-D",
  } as const;
  const result: ServiceResult = {
    schema_version: "service-v1",
    run: view().task.run,
    result_version: 1,
    execution_status: "succeeded",
    business_status: "completed",
    artifact_status: "written",
    durable: true,
    findings: [],
    verification: null,
    artifacts: [
      {
        schema_version: "artifact-manifest-v2",
        artifact_id: "33333333-3333-4333-8333-333333333333",
        context: primary,
        contexts: [primary, secondary],
        scope: "review_contexts",
        media_type: "application/pdf",
        verification: "local_writer_reopened",
        publication: "fenced",
        content_hash: "a".repeat(64),
        field_ids: ["one", "two"],
        page_count: 2,
        template_hash: "b".repeat(64),
        field_map_hash: "c".repeat(64),
        font_hash: "d".repeat(64),
        writer_version: "synthetic-v1",
        manifest_digest: "e".repeat(64),
      },
    ],
  };
  expect(validateResponse("ServiceResult", result)).toBe(true);
  const client = { readJobResult: vi.fn().mockResolvedValue(result) } as unknown as ReviewClient;
  render(<ResultPanel jobId="job-1" client={client} />);
  await userEvent.setup().click(screen.getByRole("button", { name: "Load current result" }));
  expect(await screen.findByText("target-A")).toBeInTheDocument();
  expect(screen.getByText("comparable-B")).toBeInTheDocument();
  expect(screen.getByText("target-C")).toBeInTheDocument();
  expect(screen.getByText("comparable-D")).toBeInTheDocument();
});
