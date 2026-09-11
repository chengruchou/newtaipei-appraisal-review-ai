import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { HumanResponse, ReviewClient, TaskView } from "@/api/client";
import { ResponseForm } from "@/features/ResponseForm";
import { citation, correctionView, subjectView } from "./fixtures";

function view(): TaskView {
  const value = correctionView();
  return {
    ...value,
    task: {
      ...value.task,
      kind: "evidence_supply",
      allowed_responses: ["supply_evidence", "reject"],
    },
  };
}

it("requires an explicit server-catalog citation and submits an authoritative correction", async () => {
  const user = userEvent.setup();
  const task = view();
  const subject = subjectView(task);
  const submit = vi.fn().mockReturnValue(new Promise(() => {}));
  render(
    <ResponseForm
      view={task}
      subject={subject}
      client={{ submitResponse: submit } as unknown as ReviewClient}
      onCommitted={vi.fn()}
      onReload={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Corrected value"), "10");
  expect(screen.getByRole("button", { name: "Review and submit" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: /forms.pdf.*page 3.*row-7/ }));
  await user.click(screen.getByRole("button", { name: "Review and submit" }));
  expect(screen.getByRole("checkbox")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Yes, submit" }));
  const sent = submit.mock.calls[0]?.[1] as HumanResponse;
  expect(sent.action).toBe("supply_evidence");
  expect(sent.correction?.proposed).toMatchObject({
    value: { type: "number", value: 10, unit: "m" },
    confidence: 0,
    evidence: [citation],
  });
  expect(Object.isFrozen(sent.correction?.proposed?.evidence[0])).toBe(true);
});

it("blocks evidence submission without authoritative subject metadata", () => {
  render(
    <ResponseForm
      view={view()}
      subject={null}
      client={{} as ReviewClient}
      onCommitted={vi.fn()}
      onReload={vi.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: "Review and submit" })).toBeDisabled();
});

it("offers no fabricated evidence when the server has no resolving region", async () => {
  const user = userEvent.setup();
  const task = view();
  task.task.evidence = [{ ...citation, bbox: [0, 0, 0, 0] }];
  const subject = subjectView(task);
  subject.observation.evidence = [];
  render(
    <ResponseForm
      view={task}
      subject={subject}
      client={{} as ReviewClient}
      onCommitted={vi.fn()}
      onReload={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Corrected value"), "10");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.getByText(/No usable server-provided citation/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review and submit" })).toBeDisabled();
});
