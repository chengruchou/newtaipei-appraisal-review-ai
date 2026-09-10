import { expect, it } from "vitest";
import { validateResponse } from "@/api/validation";
import { subjectView, view } from "./fixtures";

it("accepts canonical task and subject projections without widening TaskView", () => {
  expect(validateResponse("TaskView", view())).toBe(true);
  expect(validateResponse("TaskSubjectView", subjectView())).toBe(true);
  expect(validateResponse("TaskView", { ...view(), observation: subjectView().observation })).toBe(
    false,
  );
});

it("accepts a complete receipt while rejecting a corrupt nested revision", () => {
  const receipt = {
    schema_version: "service-v1",
    task_id: view().task.task_id,
    consumed_version: 1,
    task_state: "answered",
    action: "confirm",
    job: {
      schema_version: "service-v1",
      case_id: "case-1",
      job_id: "33333333-3333-3333-3333-333333333333",
    },
    job_status: "queued",
    revision: view().task.run.revision,
    resumed_run: null,
    superseded_task_ids: [],
  };
  expect(validateResponse("ResponseReceipt", receipt)).toBe(true);
  expect(
    validateResponse("ResponseReceipt", {
      ...receipt,
      revision: { ...receipt.revision, material_digest: "not-a-digest" },
    }),
  ).toBe(false);
});
