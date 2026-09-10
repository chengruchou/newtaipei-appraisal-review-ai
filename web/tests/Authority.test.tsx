/**
 * #25 forbids the UI presenting a model proposal as an approved fact, and forbids faking a
 * highlight for a citation that cannot be located. Both are easy to get wrong by making the
 * page look tidier than the data actually is.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { components } from "@/api/schema";
import { ValueAuthority } from "@/ui/Authority";
import { EvidenceList, locatable } from "@/ui/Evidence";

import { citation, unlocatable } from "./fixtures";

type ValueRevision = components["schemas"]["ValueRevision-Output"];

function change(overrides: Partial<ValueRevision> = {}): ValueRevision {
  return {
    schema_version: "service-v1",
    subject_id: "road_width:target",
    original: {
      schema_version: "service-v1",
      state: "present",
      value: { type: "number", value: 10, unit: "m" },
      raw_text: "10 m",
      unit: "m",
      confidence: 0.91,
      evidence: [],
    },
    proposed: null,
    corrected: null,
    corrected_by: null,
    ...overrides,
  } as unknown as ValueRevision;
}

describe("ValueAuthority", () => {
  it("labels a proposal as not accepted", () => {
    render(
      <ValueAuthority
        change={change({
          proposed: {
            schema_version: "service-v1",
            state: "present",
            value: { type: "number", value: 12, unit: "m" },
            raw_text: "12 m",
            unit: "m",
            confidence: null,
            evidence: [],
          },
        })}
      />,
    );

    expect(screen.getByText(/proposed — not accepted/i)).toBeInTheDocument();
    expect(screen.getByText(/this is not a decision/i)).toBeInTheDocument();
    // The proposal must never be the only value on screen, or it reads as the answer.
    expect(screen.getByText("Observed")).toBeInTheDocument();
  });

  it("names the human a correction is attributed to", () => {
    render(
      <ValueAuthority
        change={change({
          corrected: {
            schema_version: "service-v1",
            state: "present",
            value: { type: "number", value: 12, unit: "m" },
            raw_text: "12 m",
            unit: "m",
            confidence: null,
            evidence: [],
          },
          corrected_by: { schema_version: "service-v1", actor_id: "reviewer-one", kind: "human" },
        })}
      />,
    );

    expect(screen.getByText(/corrected by reviewer/i)).toBeInTheDocument();
    expect(screen.getByText("reviewer-one")).toBeInTheDocument();
  });

  it("does not let an extractor confidence read as an approval", () => {
    render(<ValueAuthority change={change()} />);

    expect(screen.getByText(/confidence is not an approval/i)).toBeInTheDocument();
  });

  it("shows a blank as blank rather than as a zero", () => {
    render(
      <ValueAuthority
        change={change({
          original: {
            schema_version: "service-v1",
            state: "blank",
            value: null,
            raw_text: "",
            unit: null,
            confidence: null,
            evidence: [],
          },
        })}
      />,
    );

    expect(screen.getByText("blank")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});

describe("Evidence", () => {
  it("treats a degenerate bounding box as unlocatable", () => {
    expect(locatable(citation)).toBe(true);
    expect(locatable(unlocatable)).toBe(false);
  });

  it("says a region cannot be located instead of drawing one anyway", () => {
    render(<EvidenceList citations={[unlocatable]} />);

    expect(screen.getByTestId("unlocatable")).toBeInTheDocument();
    expect(screen.getByText(/no area is highlighted/i)).toBeInTheDocument();
    expect(screen.getByText(/open page 3 of forms\.pdf/i)).toBeInTheDocument();
  });

  it("warns when a task cites nothing at all", () => {
    render(<EvidenceList citations={[]} />);

    expect(screen.getByText(/cites no evidence/i)).toBeInTheDocument();
  });
});
