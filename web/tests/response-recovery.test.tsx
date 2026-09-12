import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { ResponseReceipt, ReviewClient, TaskView } from "@/api/client";
import { ServiceError, TransportError } from "@/api/problems";
import { ResponseForm } from "@/features/ResponseForm";
import { correctionView, subjectView, view } from "./fixtures";

// Mocked component unit regressions only; these do not establish real API acceptance.

const receipt = {
  task_id: view().task.task_id,
  consumed_version: 1,
  action: "confirm",
  revision: null,
  job_status: "waiting_for_human",
  superseded_task_ids: [],
} as unknown as ResponseReceipt;
function client() {
  const operations = {
    submitResponse: vi.fn().mockRejectedValue(new TransportError("Lost response")),
    readResponse: vi.fn().mockResolvedValue(receipt),
    readTask: vi.fn().mockResolvedValue(view()),
  };
  return { operations, api: operations as unknown as ReviewClient };
}
async function submit() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Review and submit" }));
  await user.click(screen.getByRole("button", { name: "Yes, submit" }));
  return user;
}
const callbacks = () => ({
  onCommitted: vi.fn(),
  onReload: vi.fn(),
  mintKey: vi.fn(() => "frozen-key"),
});

it("recovers an unknown response after navigation even when the task is now answered", async () => {
  const { operations, api } = client();
  const props = callbacks();
  const mounted = render(<ResponseForm view={view()} client={api} {...props} />);
  const user = await submit();
  await screen.findByRole("button", { name: "Check submission status" });
  mounted.unmount();
  render(<ResponseForm view={view({ version: 2, state: "answered" })} client={api} {...props} />);
  expect(operations.submitResponse).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Check submission status" }));
  await screen.findByText("Response recorded");
  expect(operations.readResponse).toHaveBeenCalledWith(view().task.task_id, "frozen-key");
  expect(operations.submitResponse).toHaveBeenCalledTimes(1);
  expect(props.mintKey).toHaveBeenCalledTimes(1);
});

it.each(["unavailable", "stale", "mismatch"])(
  "does not resend after an %s recovery",
  async (condition) => {
    const { operations, api } = client();
    if (condition === "unavailable")
      operations.readResponse.mockRejectedValue(new ServiceError("capability_unavailable", 503));
    if (condition === "stale") {
      operations.readResponse.mockRejectedValue(new ServiceError("not_found", 404));
      operations.readTask.mockResolvedValue(view({ version: 2, state: "superseded" }));
    }
    if (condition === "mismatch")
      operations.readResponse.mockResolvedValue({ ...receipt, action: "correct" });
    render(<ResponseForm view={view()} client={api} {...callbacks()} />);
    const user = await submit();
    await user.click(await screen.findByRole("button", { name: "Check submission status" }));
    expect(screen.queryByText("Response recorded")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send again" })).not.toBeInTheDocument();
    expect(operations.submitResponse).toHaveBeenCalledTimes(1);
    if (condition === "stale")
      expect(await screen.findByRole("button", { name: "Reload this task" })).toBeVisible();
  },
);

it("keeps a pending write across unmount without sending it from mount", async () => {
  const { operations, api } = client();
  let resolve: (receipt: ResponseReceipt) => void = () => {};
  operations.submitResponse.mockImplementation(
    () =>
      new Promise<ResponseReceipt>((complete) => {
        resolve = complete;
      }),
  );
  const props = callbacks();
  const mounted = render(<ResponseForm view={view()} client={api} {...props} />);
  await submit();
  mounted.unmount();
  render(<ResponseForm view={view()} client={api} {...props} />);
  expect(screen.getByRole("button", { name: "Submitting…" })).toBeDisabled();
  expect(operations.submitResponse).toHaveBeenCalledTimes(1);
  await act(() => {
    resolve(receipt);
    return Promise.resolve();
  });
  expect(await screen.findByText("Response recorded")).toBeVisible();
  expect(operations.submitResponse).toHaveBeenCalledTimes(1);
});

it("a different authenticated client cannot recover another account's pending command", async () => {
  const first = client();
  const mounted = render(<ResponseForm view={view()} client={first.api} {...callbacks()} />);
  await submit();
  mounted.unmount();
  const second = client();
  render(<ResponseForm view={view()} client={second.api} {...callbacks()} />);
  expect(screen.queryByRole("button", { name: "Check submission status" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review and submit" })).toBeVisible();
  expect(second.operations.readResponse).not.toHaveBeenCalled();
  expect(second.operations.submitResponse).not.toHaveBeenCalled();
});

it("isolates a pending command from the same task identifier in a different case", async () => {
  const { operations, api } = client();
  const props = callbacks();
  const first = view();
  const mounted = render(<ResponseForm view={first} client={api} {...props} />);
  const user = await submit();
  await screen.findByRole("button", { name: "Check submission status" });
  const otherCase = structuredClone(first);
  otherCase.task.run.revision.case_id = "different-case";
  mounted.rerender(<ResponseForm view={otherCase} client={api} {...props} />);
  expect(screen.queryByRole("button", { name: "Check submission status" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review and submit" })).toBeEnabled();
  expect(operations.submitResponse).toHaveBeenCalledTimes(1);
  mounted.rerender(<ResponseForm view={first} client={api} {...props} />);
  await user.click(screen.getByRole("button", { name: "Check submission status" }));
  expect(await screen.findByText("Response recorded")).toBeVisible();
  expect(operations.readResponse).toHaveBeenCalledWith(first.task.task_id, "frozen-key");
});

it.each(["version", "revision", "side", "result", "actions", "subject"])(
  "drops an unsubmitted correction when its %s binding changes",
  async (binding) => {
    const { operations, api } = client();
    const props = callbacks();
    const first = correctionView();
    const renderForm = (value: TaskView) => (
      <ResponseForm
        view={value}
        subject={{ ...subjectView(value), subject_id: value.subject_id! }}
        client={api}
        {...props}
      />
    );
    const mounted = render(renderForm(first));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Corrected value"), "12");
    await user.click(screen.getByRole("button", { name: "Review and submit" }));
    const changed = structuredClone(first);
    if (binding === "version") changed.task.version += 1;
    if (binding === "revision") changed.task.run.revision.revision_id = "new-revision";
    if (binding === "side") changed.task.side!.input_digest = "b".repeat(64);
    if (binding === "result") changed.task.result_digest = "c".repeat(64);
    if (binding === "actions") changed.task.allowed_responses = ["correct"];
    if (binding === "subject") changed.subject_id = "different-subject";
    mounted.rerender(renderForm(changed));
    expect(screen.getByLabelText("Corrected value")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Yes, submit" })).not.toBeInTheDocument();
    expect(operations.submitResponse).not.toHaveBeenCalled();
    expect(operations.readResponse).not.toHaveBeenCalled();
  },
);

it("does not carry a committed receipt into a changed revision or task state", async () => {
  const { operations, api } = client();
  operations.submitResponse.mockResolvedValue(receipt);
  const props = callbacks();
  const mounted = render(<ResponseForm view={view()} client={api} {...props} />);
  await submit();
  expect(await screen.findByText("Response recorded")).toBeVisible();
  mounted.rerender(<ResponseForm view={view({ state: "superseded" })} client={api} {...props} />);
  expect(screen.queryByText("Response recorded")).not.toBeInTheDocument();
  expect(screen.getByText(/can no longer be answered/)).toBeVisible();
  expect(operations.submitResponse).toHaveBeenCalledTimes(1);
});

it("retains the frozen unknown command across revisions but never resends against newer material", async () => {
  const { operations, api } = client();
  const props = callbacks();
  const mounted = render(<ResponseForm view={view()} client={api} {...props} />);
  const user = await submit();
  await screen.findByRole("button", { name: "Check submission status" });
  const changed = view({ version: 2 });
  changed.task.run.revision.revision_id = "new-revision";
  operations.readResponse.mockRejectedValue(new ServiceError("not_found", 404));
  operations.readTask.mockResolvedValue(changed);
  mounted.rerender(<ResponseForm view={changed} client={api} {...props} />);
  expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  expect(screen.getByText(/Recovering a response from an earlier task binding/)).toBeVisible();
  expect(screen.getByText(/Original revision/)).toHaveTextContent("r1");
  await user.click(screen.getByRole("button", { name: "Check submission status" }));
  expect(await screen.findByRole("button", { name: "Reload this task" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Send again" })).not.toBeInTheDocument();
  expect(operations.readResponse).toHaveBeenCalledWith(view().task.task_id, "frozen-key");
  expect(operations.submitResponse).toHaveBeenCalledTimes(1);
  expect(props.mintKey).toHaveBeenCalledTimes(1);
});
