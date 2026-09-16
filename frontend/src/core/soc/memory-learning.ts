import type { SocMemoryLearningView } from "./types";

export function memoryLearningHref(view: SocMemoryLearningView): string | null {
  if (view.action === "view_memory" && view.memory_id) {
    return `/workspace/soc/memory/records/${encodeURIComponent(view.memory_id)}`;
  }
  if (
    (view.action === "review" || view.action === "view_history") &&
    view.candidate_id
  ) {
    return `/workspace/soc/review/memory-candidates/${encodeURIComponent(view.candidate_id)}`;
  }
  return null;
}
