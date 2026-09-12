/** Component unit regression only; supplied shapes are not real-case acceptance data. */
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ReviewClient, type CaseContextView, type SourceCitation } from "@/api/client";
import { EvidenceComparison } from "@/features/WorkbenchJob";
import { EvidenceList } from "@/ui/Evidence";
import { citation, view } from "./fixtures";

type Data = ComponentProps<typeof EvidenceComparison>["data"];
const context = {
  scope: "regional" as const,
  target_id: "unit-target",
  comparable_id: "unit-comparable",
};

function citations(count: number): SourceCitation[] {
  return Array.from({ length: count }, (_, i) => ({
    ...citation,
    document_id: "unit-rule-document",
    version: "unit-v2",
    page: i + 1,
    region_id: `unit-region-${i}`,
    bbox: [i, i + 1, i + 10, i + 20],
    excerpt: `Unmodified unit citation ${i}`,
  }));
}

function data(): Data {
  const refs = citations(8);
  const rule: CaseContextView["rules"][number] = {
    schema_version: "service-v1",
    reference: {
      schema_version: "service-v1",
      rule_set_id: "unit-scoped-rules",
      version: "scope-v3",
      content_hash: "c".repeat(64),
      context,
    },
    applicability: { jurisdiction: "unit-jurisdiction", land_use_category: "unit-use" },
    declared_status: "candidate",
    evidence: refs,
    zone: "unit-zone",
  };
  return {
    context: {
      schema_version: "service-v1",
      job: { schema_version: "service-v1", case_id: "case-1", job_id: "unit-job" },
      revision: view().task.run.revision,
      identity: null,
      documents: [],
      observations: [],
      selections: [],
      rules: [
        rule,
        {
          ...rule,
          reference: {
            ...rule.reference,
            rule_set_id: "other-scoped-rules",
            context: { ...context, comparable_id: "other-comparable" },
          },
        },
      ],
    },
    result: null,
    assessmentError: false,
    assessment: {
      schema_version: "service-v1",
      scope: "paused_review",
      run: view().task.run,
      status: "needs_review",
      verification: null,
      coverage: { required: [], verified: [], missing: [] },
      findings: [
        {
          id: "unit-trust",
          kind: "approval",
          status: "needs_review",
          trace: "Unit trust record without comparison context",
          evidence: [],
        },
        {
          id: "unit-no-evidence",
          kind: "factor",
          status: "needs_review",
          context,
          factor_id: "unit-factor-without-evidence",
          trace: "Unit factor without citations",
          evidence: [],
        },
        {
          id: "unit-no-factor",
          kind: "source_identity",
          status: "verified",
          context,
          trace: "Unit context without a factor",
          evidence: [refs[0]!],
        },
        {
          id: "unit-actual-context",
          kind: "factor",
          status: "needs_review",
          context,
          factor_id: "unit-factor",
          trace: "Unit contextual factor with evidence",
          evidence: [
            {
              ...refs[0]!,
              page: 20,
              region_id: "finding-only-region",
              excerpt: "Finding citation from the same document but another region",
            },
          ],
        },
      ],
    },
  };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

function show(value = data(), query = "") {
  const fetch = vi.fn<typeof globalThis.fetch>();
  const client = new ReviewClient({ baseUrl: "", token: () => Promise.resolve(null), fetch });
  const mounted = render(
    <MemoryRouter initialEntries={[`/jobs/unit-job/evidence${query}`]}>
      <EvidenceComparison data={value} client={client} jobId="unit-job" />
      <LocationProbe />
    </MemoryRouter>,
  );
  return { ...mounted, fetch };
}

describe("evidence selection and scope (unit regression)", () => {
  it("defaults to the first contextual factor with evidence without changing the URL or reading sources", () => {
    const { fetch } = show(data(), "?view=source");
    expect(screen.getByRole("combobox", { name: "Current finding" })).toHaveValue("3");
    expect(screen.getByText("Unit contextual factor with evidence")).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent("/jobs/unit-job/evidence?view=source");
    expect(screen.getByRole("region", { name: "unit-scoped-rules scope-v3" })).toBeVisible();
    expect(
      screen.queryByRole("region", { name: "other-scoped-rules scope-v3" }),
    ).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("preserves an explicit trust finding and does not associate every rule scope with it", () => {
    show(data(), "?finding=0");
    expect(screen.getByRole("combobox")).toHaveValue("0");
    expect(screen.getByText("Unit trust record without comparison context")).toBeVisible();
    expect(screen.getByText(/This finding has no comparison context/)).toBeVisible();
    expect(screen.queryByText(/Unmodified unit citation/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "unit-scoped-rules scope-v3" }),
    ).not.toBeInTheDocument();
  });

  it.each(["-1", "999", "unknown"])(
    "does not replace explicit invalid selection %s with a default",
    (index) => {
      show(data(), `?finding=${index}`);
      expect(screen.getByText(/Select an available finding/)).toBeVisible();
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
      expect(screen.getByTestId("location")).toHaveTextContent(`finding=${index}`);
    },
  );

  it("falls back to the first record when none has context, factor and evidence", () => {
    const value = data();
    value.assessment!.findings = value.assessment!.findings.slice(0, 3);
    show(value);
    expect(screen.getByRole("combobox")).toHaveValue("0");
    expect(screen.getByText(/No scoped rule association is inferred/)).toBeVisible();
  });

  it("retains a finding citation even when its document also appears in a scoped rule set", () => {
    show();
    const finding = screen.getByRole("group", { name: "Finding citations" });
    expect(
      within(finding).getByText("Finding citation from the same document but another region"),
    ).toBeVisible();
    expect(within(finding).getByText("finding-only-region")).toBeVisible();
    expect(within(finding).getByText("20")).toBeVisible();
    expect(screen.getByText(/A scoped rule set may cover several factors/)).toBeVisible();
    expect(screen.getByText(/Finding-reported rule/)).toHaveTextContent(
      "Not specified / Version not supplied",
    );
  });

  it("labels supplied rule identity verbatim while leaving the broader rule group scoped", () => {
    const value = data();
    Object.assign(value.assessment!.findings[3]!, {
      rule_id: "specific-unit-rule",
      rule_version: "rule-v9",
    });
    show(value);
    expect(screen.getByText(/Finding-reported rule/)).toHaveTextContent(
      "specific-unit-rule / rule-v9",
    );
    expect(
      screen.getByRole("heading", { name: "Rule references for this comparison scope" }),
    ).toBeVisible();
    // The loopback provenance moved behind the 技術說明 fold; it stays available verbatim
    // but is no longer part of the main copy.
    expect(screen.getByText(/separately paired loopback source provider/)).toBeInTheDocument();
    expect(screen.getByText(/separately paired loopback source provider/)).not.toBeVisible();
  });

  it("changes only the requested finding query and performs no writes or source fetches", async () => {
    const { fetch } = show(data(), "?view=source");
    await userEvent.setup().selectOptions(screen.getByRole("combobox"), "0");
    expect(screen.getByTestId("location")).toHaveTextContent("?view=source&finding=0");
    expect(screen.getByText("Unit trust record without comparison context")).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("bounded citation lists (unit regression)", () => {
  it("shows three citations and preserves all remaining raw fields and duplicate occurrences", async () => {
    const refs = citations(6);
    refs[4] = { ...refs[0]! };
    const original = structuredClone(refs);
    const loadSource = vi.fn<() => Promise<ArrayBuffer>>();
    const submit = vi.fn((event: React.FormEvent) => event.preventDefault());
    render(
      <form onSubmit={submit}>
        <EvidenceList citations={refs} loadSource={loadSource} />
      </form>,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getAllByRole("listitem", { hidden: true })).toHaveLength(6);
    const button = screen.getByRole("button", { name: "Show all 6 citations (3 more)" });
    const controlled = document.getElementById(button.getAttribute("aria-controls")!);
    expect(controlled).not.toBeVisible();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveStyle({ maxWidth: "100%", whiteSpace: "normal" });
    button.focus();
    const user = userEvent.setup();
    await user.keyboard("{Enter}");
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
    expect(controlled).toBeVisible();
    expect(screen.getAllByText("Unmodified unit citation 0")).toHaveLength(2);
    const last = screen.getAllByRole("listitem")[5]!;
    fireEvent.click(within(last).getByText("Source identity and coordinates"));
    expect(within(last).getByText(refs[5]!.content_hash)).toBeVisible();
    expect(within(last).getByText(refs[5]!.bbox.join(", "))).toBeVisible();
    expect(within(last).getByText(refs[5]!.excerpt)).toBeVisible();
    button.focus();
    await user.keyboard(" ");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(refs).toEqual(original);
    expect(loadSource).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("retains expansion through identical polling data but resets it when the citation identity changes", async () => {
    const refs = citations(5);
    const mounted = render(<EvidenceList citations={refs} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Show all 5 citations (2 more)" }));
    mounted.rerender(<EvidenceList citations={structuredClone(refs)} />);
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
    mounted.rerender(
      <EvidenceList citations={refs.map((ref) => ({ ...ref, content_hash: "b".repeat(64) }))} />,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("bounds each scoped rule list independently and makes all its citations available", async () => {
    const { fetch } = show();
    const group = screen.getByRole("group", { name: "Scoped rule references unit-scoped-rules" });
    expect(within(group).getAllByRole("listitem")).toHaveLength(3);
    await userEvent
      .setup()
      .click(within(group).getByRole("button", { name: "Show all 8 citations (5 more)" }));
    expect(within(group).getAllByRole("listitem")).toHaveLength(8);
    expect(within(group).getByText("Unmodified unit citation 7")).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });
});
