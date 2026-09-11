import { useMemo, useSyncExternalStore } from "react";
import type { HumanResponse, ResponseReceipt, ReviewClient, TaskView } from "@/api/client";
import type { ServiceError } from "@/api/problems";

export type ResponsePhase =
  | { name: "editing" }
  | { name: "confirming" | "submitting" | "checking" | "retryable"; command: HumanResponse }
  | { name: "committed"; receipt: ResponseReceipt }
  | { name: "conflict" }
  | { name: "unreachable"; command: HumanResponse }
  | { name: "refused"; error: ServiceError };
interface Draft {
  action: HumanResponse["action"];
  selectedCitations: string[];
  correctedText: string;
  phase: ResponsePhase;
}
interface Store {
  binding: string;
  read: () => Draft;
  subscribe: (listener: () => void) => () => void;
  change: (patch: Partial<Draft>) => void;
}
// Tab-memory only, scoped to one authenticated client. Unknown submissions retain
// their original exact command across task-version changes for receipt recovery.
// Navigation never submits, and a new account cannot retrieve an old client's command.
const sessions = new WeakMap<ReviewClient, Map<string, Store>>();
function bindingFor(view: TaskView): string {
  return JSON.stringify([
    view.task.version,
    view.task.state,
    view.task.run,
    view.task.side,
    view.task.result_digest,
    view.task.allowed_responses,
    view.subject_id,
  ]);
}
function draftFor(client: ReviewClient, view: TaskView): Store {
  let tasks = sessions.get(client);
  if (!tasks) {
    tasks = new Map();
    sessions.set(client, tasks);
  }
  const key = JSON.stringify([view.task.run.revision.case_id, view.task.task_id]);
  const binding = bindingFor(view);
  let store = tasks.get(key);
  const oldPhase = store?.read().phase.name;
  const preserve = oldPhase && ["submitting", "checking", "unreachable"].includes(oldPhase);
  if (!store || (store.binding !== binding && !preserve)) {
    let draft: Draft = {
      action: view.task.allowed_responses[0] ?? "reject",
      selectedCitations: [],
      correctedText: "",
      phase: { name: "editing" },
    };
    const listeners = new Set<() => void>();
    store = {
      binding,
      read: () => draft,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => {
          listeners.delete(listener);
        };
      },
      change: (patch) => {
        draft = { ...draft, ...patch };
        listeners.forEach((listener) => listener());
      },
    };
    tasks.set(key, store);
  }
  return store;
}
export function useResponseDraft(client: ReviewClient, view: TaskView) {
  const store = useMemo(() => draftFor(client, view), [client, view]);
  return [
    useSyncExternalStore(store.subscribe, store.read),
    store,
    store.binding === bindingFor(view),
  ] as const;
}
