/**
 * #25's submission-safety criteria, as behaviour rather than as prose.
 *
 * The cases that matter are the ones where a reasonable-looking form does the wrong
 * thing: sending twice while the first request is in flight, minting a new idempotency
 * key when the reviewer retries after a timeout, or offering a blind resend after a
 * conflict when the reviewer needs to look at newer state first.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { HumanResponse, ResponseReceipt, ReviewClient } from "@/api/client";
import { ServiceError, TransportError } from "@/api/problems";
import { ResponseForm } from "@/features/ResponseForm";

import { correctionView, SUBJECT, view } from "./fixtures";

const receipt = {
  schema_version: "service-v1",
  task_id: "11111111-1111-1111-1111-111111111111",
  consumed_version: 1,
  task_state: "answered",
  action: "confirm",
  job: { schema_version: "service-v1", case_id: "case-1", job_id: "job-1" },
  job_status: "queued",
  revision: {
    schema_version: "service-v1",
    case_id: "case-1",
    revision_id: "r2",
    material_digest: "b".repeat(64),
  },
  resumed_run: null,
  superseded_task_ids: [],
} as unknown as ResponseReceipt;

function clientWith(submit: ReturnType<typeof vi.fn>): ReviewClient {
  return { submitResponse: submit } as unknown as ReviewClient;
}

async function reachConfirmation(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /review and submit/i }));
}

describe("ResponseForm", () => {
  it("offers only the actions the task itself allows", () => {
    render(
      <ResponseForm
        view={view()}
        client={clientWith(vi.fn())}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={() => "key-1"}
      />,
    );

    expect(screen.getByRole("radio", { name: /confirm this observation/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /refuse to confirm/i })).toBeInTheDocument();
    // A correction is not permitted on a confirmation task, so the form must not offer it.
    expect(screen.queryByRole("radio", { name: /correction/i })).not.toBeInTheDocument();
  });

  it("requires an explicit confirmation before it submits anything", async () => {
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue(receipt);
    render(
      <ResponseForm
        view={view()}
        client={clientWith(submit)}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={() => "key-1"}
      />,
    );

    await reachConfirmation(user);

    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /yes, submit/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /go back/i })).toBeInTheDocument();
  });

  it("cannot be submitted twice while the first request is still in flight", async () => {
    const user = userEvent.setup();
    let release: (value: ResponseReceipt) => void = () => {};
    const submit = vi.fn().mockReturnValue(
      new Promise<ResponseReceipt>((resolve) => {
        release = resolve;
      }),
    );
    render(
      <ResponseForm
        view={view()}
        client={clientWith(submit)}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={() => "key-1"}
      />,
    );

    await reachConfirmation(user);
    const confirm = screen.getByRole("button", { name: /yes, submit/i });
    await user.click(confirm);
    // The reviewer double-clicks, which is the ordinary way this goes wrong.
    await user.click(screen.getByRole("button", { name: /submitting/i }));

    expect(submit).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /submitting/i })).toBeDisabled();
    release(receipt);
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
  });

  it("resends after a timeout under the same key rather than minting a new one", async () => {
    const user = userEvent.setup();
    const submit = vi
      .fn()
      .mockRejectedValueOnce(new TransportError("The service did not answer in time."))
      .mockResolvedValueOnce(receipt);
    render(
      <ResponseForm
        view={view()}
        client={clientWith(submit)}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={() => "key-1"}
      />,
    );

    await reachConfirmation(user);
    await user.click(screen.getByRole("button", { name: /yes, submit/i }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: /send again/i }));
    await screen.findByRole("status");

    const keys = submit.mock.calls.map((call) => (call[1] as HumanResponse).idempotency_key);
    // Two attempts at one decision must be one write, which is only true if the key is
    // identical. A fresh key here would commit the reviewer's answer twice.
    expect(keys).toEqual(["key-1", "key-1"]);
  });

  it("offers a reload rather than a resend after a conflict", async () => {
    const user = userEvent.setup();
    const submit = vi.fn().mockRejectedValue(new ServiceError("version_conflict", 409));
    const onReload = vi.fn();
    render(
      <ResponseForm
        view={view()}
        client={clientWith(submit)}
        onCommitted={vi.fn()}
        onReload={onReload}
        mintKey={() => "key-1"}
      />,
    );

    await reachConfirmation(user);
    await user.click(screen.getByRole("button", { name: /yes, submit/i }));
    await screen.findByRole("alert");

    expect(screen.queryByRole("button", { name: /send again/i })).not.toBeInTheDocument();
    expect(screen.getByText(/nothing was saved/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /reload this task/i }));
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it("refuses to answer a task that is no longer open", () => {
    render(
      <ResponseForm
        view={view({ state: "superseded" } as never)}
        client={clientWith(vi.fn())}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={() => "key-1"}
      />,
    );

    expect(screen.getByText(/can no longer be answered/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /review and submit/i })).not.toBeInTheDocument();
  });

  it("sends the server's subject name verbatim instead of rebuilding it", async () => {
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue(receipt);
    render(
      <ResponseForm
        view={correctionView()}
        client={clientWith(submit)}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={() => "key-1"}
      />,
    );

    await user.type(screen.getByLabelText(/corrected value/i), "12");
    await reachConfirmation(user);
    await user.click(screen.getByRole("button", { name: /yes, submit/i }));
    await screen.findByRole("status");

    const sent = submit.mock.calls[0]?.[1] as HumanResponse;
    // Escaped exactly as the server canonicalized it. A browser rebuilding this from the
    // comparison context would emit the unescaped Chinese and be rejected.
    expect(sent.correction?.subject_id).toBe(SUBJECT);
    expect(sent.correction?.subject_id).toContain("\\u677f\\u6a4b");
  });

  it("will not submit a correction with no usable value", async () => {
    const user = userEvent.setup();
    render(
      <ResponseForm
        view={correctionView()}
        client={clientWith(vi.fn())}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={() => "key-1"}
      />,
    );

    expect(screen.getByRole("button", { name: /review and submit/i })).toBeDisabled();
    await user.type(screen.getByLabelText(/corrected value/i), "not a number");
    expect(screen.getByRole("button", { name: /review and submit/i })).toBeDisabled();
  });
});
