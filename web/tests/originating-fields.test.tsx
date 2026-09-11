import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReviewClient, ServiceResult } from "@/api/client";
import { ResultPanel } from "@/features/ResultPanel";
import { view } from "./fixtures";

function result(origins: string[] = []): ServiceResult {
  const finding = (id: string, kind: string, value: string | null) => ({
    id: `observed/${id}`,
    kind,
    status: "needs_review" as const,
    context: null,
    factor_id: null,
    rule_id: null,
    rule_version: null,
    observed: value,
    expected: value,
    evidence: [],
    trace: "Review source evidence before filling.",
    ...(origins.length ? { originating_field_ids: origins } : {}),
  });
  return {
    schema_version: "service-v1",
    run: view().task.run,
    result_version: 1,
    execution_status: "succeeded",
    business_status: "needs_review",
    artifact_status: "not_requested",
    durable: true,
    findings: [
      finding("field-a", "source_purpose", null),
      finding("field-a", "observed_source_binding", null),
      finding("field-b", "observed_source_binding", null),
      finding("calculated-total", "observed_unresolved", "9"),
    ],
    verification: null,
    artifacts: [],
  };
}

describe("originating source-evidence fields", () => {
  it.each(["regional/copied-total", "regional/\u8907\u88fd\u5408\u8a08", "<img src=x>", ""])(
    "links existing identifier text safely (%s)",
    async (fieldId) => {
      const payload = result([fieldId]);
      for (const finding of payload.findings) {
        if (finding.id === "observed/field-a") finding.id = `observed/${fieldId}`;
      }
      const client = {
        readJobResult: vi.fn().mockResolvedValue(payload),
      } as unknown as ReviewClient;
      render(<ResultPanel jobId="job-1" client={client} />);
      await userEvent.setup().click(screen.getByRole("button", { name: "Load current result" }));
      const diagnostic = await screen.findByRole("complementary", {
        name: "Fields requiring source evidence",
      });
      const link = within(diagnostic).getByRole("link", {
        name: fieldId || '"" (empty field ID)',
      });
      expect(link).toHaveAttribute("href", "#review-finding-1");
      expect(document.querySelector("#review-finding-1")).toHaveTextContent(
        "observed_source_binding",
      );
      expect(diagnostic.querySelector("img")).toBeNull();
      expect(screen.getByText("Observed: 9. Expected: 9.")).toBeInTheDocument();
    },
  );

  it.each([["field-a"], ["field-a", "field-b"]])(
    "links original fields once and preserves the whole-case PDF gate (%s)",
    async (...origins) => {
      const client = {
        readJobResult: vi.fn().mockResolvedValue(result(origins)),
      } as unknown as ReviewClient;
      render(<ResultPanel jobId="job-1" client={client} />);
      await userEvent.setup().click(screen.getByRole("button", { name: "Load current result" }));
      const diagnostic = await screen.findByRole("complementary", {
        name: "Fields requiring source evidence",
      });
      expect(within(diagnostic).getAllByRole("link")).toHaveLength(origins.length);
      for (const field of origins) {
        const link = within(diagnostic).getByRole("link", { name: field });
        const destination = document.querySelector(link.getAttribute("href")!);
        expect(destination).not.toBeNull();
        expect(destination).toHaveTextContent("observed_source_binding");
      }
      expect(diagnostic).toHaveTextContent("PDF filling remains blocked for the entire case");
      expect(screen.getByText("Observed: 9. Expected: 9.")).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Download verified PDF" }),
      ).not.toBeInTheDocument();
    },
  );

  it("clears an earlier origin mapping when a new result is loaded", async () => {
    const client = {
      readJobResult: vi
        .fn()
        .mockResolvedValueOnce(result(["field-a"]))
        .mockResolvedValueOnce(result()),
    } as unknown as ReviewClient;
    render(<ResultPanel jobId="job-1" client={client} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Load current result" }));
    expect(
      await screen.findByRole("complementary", { name: "Fields requiring source evidence" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load current result" }));
    expect(
      screen.queryByRole("complementary", { name: "Fields requiring source evidence" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Observed: 9. Expected: 9.")).toBeInTheDocument();
  });

  it("accepts legacy findings without inventing originating fields", async () => {
    const client = {
      readJobResult: vi.fn().mockResolvedValue(result()),
    } as unknown as ReviewClient;
    render(<ResultPanel jobId="job-1" client={client} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Load current result" }));
    expect(
      screen.queryByRole("complementary", { name: "Fields requiring source evidence" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Observed: 9. Expected: 9.")).toBeInTheDocument();
  });
});
