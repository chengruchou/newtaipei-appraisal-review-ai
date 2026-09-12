import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ReviewClient } from "@/api/client";
import { ServiceError, TransportError } from "@/api/problems";
import { ResponseForm } from "@/features/ResponseForm";
import { correctionView, subjectView } from "./fixtures";

const message = "Service operation could not be completed.";
const ambiguous = [
  [502, "<html>Bad gateway</html>"],
  [504, "<html>Gateway timeout</html>"],
  [503, "<html>Service unavailable</html>"],
  [403, "<html>Proxy access denied</html>"],
  [422, '{"code":"invalid_request","message":"upstream unavailable"}'],
  [502, JSON.stringify({ code: "execution_failed", message })],
  [409, JSON.stringify({ code: "invalid_request", message })],
  [500, '{"code":"constructor"}'],
  [500, "null"],
] as const;

it.each(ambiguous)("retains an unknown write outcome for HTTP %s body %s", async (status, body) => {
  const client = new ReviewClient({
    baseUrl: "",
    token: () => Promise.resolve(null),
    fetch: vi.fn(() => Promise.resolve(new Response(body, { status }))),
    timeoutMs: 5000,
  });
  await expect(client.submitResponse("t", {} as never)).rejects.toBeInstanceOf(TransportError);
});

it.each([
  [403, "unauthorized"],
  [404, "not_found"],
  [409, "version_conflict"],
  [422, "invalid_request"],
  [500, "execution_failed"],
  [503, "capability_unavailable"],
] as const)("keeps validated HTTP %s %s as a definitive rejection", async (status, code) => {
  const client = new ReviewClient({
    baseUrl: "",
    token: () => Promise.resolve(null),
    fetch: vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ schema_version: "service-v1", code, message }), { status }),
      ),
    ),
    timeoutMs: 5000,
  });
  await expect(client.submitResponse("t", {} as never)).rejects.toBeInstanceOf(ServiceError);
  await expect(client.submitResponse("t", {} as never)).rejects.toMatchObject({ code, status });
});

it.each([502, 504])(
  "freezes the original correction and key after a gateway %s",
  async (status) => {
    const user = userEvent.setup();
    const fetch = vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST")
        return Promise.resolve(new Response("<html>Gateway failure</html>", { status }));
      if ((url instanceof Request ? url.url : String(url)).includes("/responses/"))
        return Promise.resolve(
          new Response(
            JSON.stringify({ schema_version: "service-v1", code: "not_found", message }),
            { status: 404 },
          ),
        );
      return Promise.resolve(new Response(JSON.stringify(correctionView()), { status: 200 }));
    });
    const client = new ReviewClient({
      baseUrl: "",
      token: () => Promise.resolve(null),
      fetch,
      timeoutMs: 5000,
    });
    const mintKey = vi.fn(() => "original-command-key");
    render(
      <ResponseForm
        view={correctionView()}
        subject={subjectView()}
        client={client}
        onCommitted={vi.fn()}
        onReload={vi.fn()}
        mintKey={mintKey}
      />,
    );
    await user.type(screen.getByLabelText("Corrected value"), "12");
    await user.click(screen.getByRole("button", { name: /review and submit/i }));
    await user.click(screen.getByRole("button", { name: /yes, submit/i }));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Corrected value")).toBeDisabled();
    expect(screen.getByRole("radio", { name: /refuse to confirm/i })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /review and submit/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /refuse to confirm/i }));
    await user.type(screen.getByLabelText("Corrected value"), "99");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /send again/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /check submission status/i }));
    await user.click(await screen.findByRole("button", { name: /send again/i }));
    expect(fetch).toHaveBeenCalledTimes(4);
    const calls = fetch.mock.calls as [string, RequestInit][];
    expect(calls.map((call) => call[1].method)).toEqual(["POST", "GET", "GET", "POST"]);
    expect(calls[3]?.[1].body).toBe(calls[0]?.[1].body);
    expect(JSON.parse(calls[0]![1].body as string)).toMatchObject({
      action: "correct",
      idempotency_key: "original-command-key",
      correction: { proposed: { value: { value: 12, unit: "m" }, confidence: 0 } },
    });
    expect(mintKey).toHaveBeenCalledTimes(1);
  },
);
