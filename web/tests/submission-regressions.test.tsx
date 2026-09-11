import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { ReviewClient } from "@/api/client";
import { ServiceError, TransportError } from "@/api/problems";
import { ResponseForm } from "@/features/ResponseForm";
import { view } from "./fixtures";

function form(submit: ReturnType<typeof vi.fn>) {
  render(
    <ResponseForm
      view={view()}
      client={
        {
          submitResponse: submit,
          readResponse: vi.fn().mockRejectedValue(new ServiceError("not_found", 404)),
          readTask: vi.fn().mockResolvedValue(view()),
        } as unknown as ReviewClient
      }
      onCommitted={vi.fn()}
      onReload={vi.fn()}
      mintKey={() => "decision-key"}
    />,
  );
}

it("locks the captured confirmation so its display and submitted action agree", async () => {
  const user = userEvent.setup();
  const submit = vi.fn().mockReturnValue(new Promise(() => {}));
  form(submit);
  await user.click(screen.getByRole("button", { name: /review and submit/i }));
  await user.click(screen.getByRole("radio", { name: /refuse to confirm/i }));
  expect(screen.getByRole("radio", { name: /refuse to confirm/i })).toBeDisabled();
  expect(screen.getByRole("radio", { name: /confirm this observation/i })).toBeChecked();
  await user.click(screen.getByRole("button", { name: /yes, submit/i }));
  expect(submit.mock.calls[0]?.[1]).toMatchObject({ action: "confirm" });
});

it("resends the exact unknown command and key even after attempted edits", async () => {
  const user = userEvent.setup();
  const submit = vi.fn().mockRejectedValue(new TransportError("Connection interrupted"));
  form(submit);
  await user.click(screen.getByRole("button", { name: /review and submit/i }));
  await user.click(screen.getByRole("button", { name: /yes, submit/i }));
  await screen.findByRole("alert");
  const original = JSON.stringify(submit.mock.calls[0]?.[1]);
  await user.click(screen.getByRole("radio", { name: /refuse to confirm/i }));
  expect(submit).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: /send again/i })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /check submission status/i }));
  await user.click(await screen.findByRole("button", { name: /send again/i }));
  expect(JSON.stringify(submit.mock.calls[1]?.[1])).toBe(original);
  expect(screen.queryByRole("button", { name: /review and submit/i })).not.toBeInTheDocument();
});
