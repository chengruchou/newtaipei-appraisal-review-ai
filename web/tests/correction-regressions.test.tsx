import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { ReviewClient, TaskSubjectView, HumanResponse } from "@/api/client";
import { ResponseForm } from "@/features/ResponseForm";
import { correctionView, SUBJECT } from "./fixtures";

const current = correctionView();
function subject(
  type: TaskSubjectView["required_type"] = "number",
  unit: string | null = "m",
): TaskSubjectView {
  return {
    schema_version: "service-v1",
    task_id: current.task.task_id,
    revision: current.task.run.revision,
    subject_id: SUBJECT,
    required_type: type,
    required_unit: unit,
    unit_required: type === "number",
    observation: {
      schema_version: "service-v1",
      state: "present",
      value:
        type === null
          ? null
          : {
              type,
              value: type === "number" ? 10 : type === "boolean" ? false : "commercial",
              unit,
            },
      raw_text: "10",
      unit,
      confidence: 0,
      evidence: [],
    },
  };
}

function form(metadata: ReturnType<typeof subject> | null) {
  const submit = vi.fn().mockReturnValue(new Promise(() => {}));
  render(
    <ResponseForm
      view={current}
      subject={metadata}
      client={{ submitResponse: submit } as unknown as ReviewClient}
      onCommitted={vi.fn()}
      onReload={vi.fn()}
    />,
  );
  return submit;
}

it.each([
  ["m", "12", 12],
  ["percent", "0", 0],
])("preserves authoritative %s including zero", async (unit, text, expected) => {
  const user = userEvent.setup();
  const submit = form(subject("number", String(unit)));
  await user.type(screen.getByLabelText(/corrected value/i), String(text));
  await user.click(screen.getByRole("button", { name: /review and submit/i }));
  await user.click(screen.getByRole("button", { name: /yes, submit/i }));
  expect((submit.mock.calls[0]?.[1] as HumanResponse).correction?.proposed).toMatchObject({
    value: { type: "number", value: expected, unit },
    unit,
    confidence: 0,
  });
});

it.each([null, subject("number", null), subject(null)])(
  "blocks correction without authoritative type/unit",
  async (metadata) => {
    const user = userEvent.setup();
    const submit = form(metadata);
    await user.type(screen.getByLabelText(/corrected value/i), "12");
    expect(screen.getByRole("button", { name: /review and submit/i })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  },
);

it("accepts a category as a category rather than coercing it to a number", async () => {
  const user = userEvent.setup();
  const submit = form(subject("category", null));
  await user.type(screen.getByLabelText(/corrected value/i), "commercial");
  await user.click(screen.getByRole("button", { name: /review and submit/i }));
  expect(screen.getByRole("button", { name: /yes, submit/i })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: /yes, submit/i }));
  expect((submit.mock.calls[0]?.[1] as HumanResponse).correction?.proposed?.value).toEqual({
    type: "category",
    value: "commercial",
    unit: null,
  });
});

it.each(["text", "boolean"] as const)("preserves authoritative %s values", async (type) => {
  const user = userEvent.setup();
  const submit = form(subject(type, null));
  await user.type(
    screen.getByLabelText(/corrected value/i),
    type === "boolean" ? "false" : "零號測試",
  );
  await user.click(screen.getByRole("button", { name: /review and submit/i }));
  await user.click(screen.getByRole("button", { name: /yes, submit/i }));
  expect((submit.mock.calls[0]?.[1] as HumanResponse).correction?.proposed?.value).toEqual({
    type,
    value: type === "boolean" ? false : "零號測試",
    unit: null,
  });
});

it("blocks metadata from another revision while still allowing rejection", async () => {
  const user = userEvent.setup();
  const metadata = subject();
  metadata.revision = { ...metadata.revision, revision_id: "other-revision" };
  const submit = form(metadata);
  await user.type(screen.getByLabelText(/corrected value/i), "12");
  expect(screen.getByRole("button", { name: /review and submit/i })).toBeDisabled();
  await user.click(screen.getByRole("radio", { name: /refuse to confirm/i }));
  await user.click(screen.getByRole("button", { name: /review and submit/i }));
  await user.click(screen.getByRole("button", { name: /yes, submit/i }));
  expect((submit.mock.calls[0]?.[1] as HumanResponse).action).toBe("reject");
});

it("locks a correction's exact displayed value and original metadata until submission", async () => {
  const user = userEvent.setup();
  const metadata = subject();
  const submit = form(metadata);
  await user.type(screen.getByLabelText(/corrected value/i), "12");
  await user.click(screen.getByRole("button", { name: /review and submit/i }));
  await user.type(screen.getByLabelText(/corrected value/i), "99");
  metadata.observation.raw_text = "mutated elsewhere";
  expect(screen.getByLabelText(/corrected value/i)).toBeDisabled();
  expect(screen.getByText(/Proposed correction \(not accepted\): 12 m/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /yes, submit/i }));
  const command = submit.mock.calls[0]?.[1] as HumanResponse;
  expect(command.correction?.proposed?.value).toMatchObject({ value: 12, unit: "m" });
  expect(command.correction?.original.raw_text).toBe("10");
  expect(Object.isFrozen(command.correction?.proposed?.value)).toBe(true);
});
