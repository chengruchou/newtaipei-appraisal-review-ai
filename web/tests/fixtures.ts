import type { HumanTask, SourceCitation, TaskView, TaskSubjectView } from "@/api/client";

const DIGEST = "a".repeat(64);

export const citation: SourceCitation = {
  document_id: "forms.pdf",
  content_hash: DIGEST,
  version: "1",
  page: 3,
  region_id: "row-7",
  bbox: [10, 20, 120, 44],
  excerpt: "臨路寬度 10 公尺",
} as unknown as SourceCitation;

export const unlocatable: SourceCitation = {
  ...citation,
  region_id: "row-9",
  bbox: [0, 0, 0, 0],
} as unknown as SourceCitation;

/** A subject name in the server's escaped canonical form, as a real case would carry. */
export const SUBJECT = '["regional","\\u677f\\u6a4b-A","\\u4e09\\u91cd-B"]:road_width:target';

export function task(overrides: Partial<HumanTask> = {}): HumanTask {
  return {
    schema_version: "service-v1",
    task_id: "11111111-1111-1111-1111-111111111111",
    run: {
      schema_version: "service-v1",
      run_id: "22222222-2222-2222-2222-222222222222",
      revision: {
        schema_version: "service-v1",
        case_id: "case-1",
        revision_id: "r1",
        material_digest: DIGEST,
      },
      attempt_id: null,
      runtime_session_id: null,
    },
    version: 1,
    kind: "fact_confirmation",
    required_permission: "confirm_observation",
    state: "open",
    side: {
      schema_version: "service-v1",
      context: { scope: "regional", target_id: "板橋-A", comparable_id: "三重-B" },
      factor_id: "road_width",
      side: "target",
      input_digest: DIGEST,
    },
    result_digest: null,
    question: "Is the target road width 10 m?",
    evidence: [citation],
    finding_ids: ["road_width"],
    allowed_responses: ["confirm", "reject"],
    ...overrides,
  } as unknown as HumanTask;
}

export function view(overrides: Partial<HumanTask> = {}): TaskView {
  return {
    schema_version: "service-v1",
    task: task(overrides),
    subject_id: SUBJECT,
  } as unknown as TaskView;
}

export function correctionView(): TaskView {
  return {
    schema_version: "service-v1",
    task: task({
      kind: "material_correction",
      required_permission: "correct_material",
      allowed_responses: ["correct", "reject"],
    }),
    subject_id: SUBJECT,
  } as unknown as TaskView;
}

export function subjectView(taskView: TaskView = correctionView()): TaskSubjectView {
  return {
    schema_version: "service-v1",
    task_id: taskView.task.task_id,
    revision: taskView.task.run.revision,
    subject_id: SUBJECT,
    required_type: "number",
    required_unit: "m",
    unit_required: true,
    observation: {
      schema_version: "service-v1",
      state: "present",
      value: { type: "number", value: 10, unit: "m" },
      raw_text: "10 m",
      unit: "m",
      confidence: 0,
      evidence: [citation],
    },
  };
}
