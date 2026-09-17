import type { MemoryCandidateReviewDraft } from "./memory-review-draft";
import type { SocMemoryBusinessLessonDraft, SocVerdict } from "./types";

export interface SocMemoryDraftContent {
  reviewer_verdict: SocVerdict | null;
  reviewer_context: string;
  apply_to_future_matches: boolean;
  selected_behavior_components: string[] | null;
  promoted_facet_values: Record<string, string[]>;
  detection_scenario: string;
  observed_event: string;
  conclusion: string;
  business_rationale: string;
  generalization_boundaries: string;
  invalidation_conditions: string;
  handling_guidance: string;
}

export interface SocMemoryWorkingDraft {
  candidate_id: string;
  candidate_revision: string;
  version: number;
  content: SocMemoryDraftContent;
  updated_by: string;
  updated_at: string;
  last_generation: SocMemoryBusinessLessonDraft | null;
  generation_job_id: string | null;
  authority: "draft_only";
}

export interface SocMemoryWorkingDraftView {
  candidate_id: string;
  candidate_revision: string;
  editable: boolean;
  stale: boolean;
  draft: SocMemoryWorkingDraft | null;
}

export function workingDraftContent(
  draft: MemoryCandidateReviewDraft,
): SocMemoryDraftContent {
  return {
    reviewer_verdict: draft.confirmedVerdict,
    reviewer_context: draft.businessContext,
    apply_to_future_matches: draft.applyToFutureMatches,
    selected_behavior_components: draft.selectedBehaviorComponents,
    promoted_facet_values: draft.promotedFacetValues,
    detection_scenario: draft.lessonDetectionScenario,
    observed_event: draft.lessonObservedEvent,
    conclusion: draft.lessonConclusion,
    business_rationale: draft.lessonBusinessRationale,
    generalization_boundaries: draft.lessonGeneralizationBoundary,
    invalidation_conditions: draft.lessonInvalidationCondition,
    handling_guidance: draft.lessonHandlingGuidance,
  };
}

export function workingDraftPatch(
  saved: SocMemoryWorkingDraft,
): Partial<MemoryCandidateReviewDraft> {
  const content = saved.content;
  return {
    confirmedVerdict: content.reviewer_verdict,
    businessContext: content.reviewer_context,
    applyToFutureMatches: content.apply_to_future_matches,
    selectedBehaviorComponents: content.selected_behavior_components,
    promotedFacetValues: content.promoted_facet_values,
    lessonDetectionScenario: content.detection_scenario,
    lessonObservedEvent: content.observed_event,
    lessonConclusion: content.conclusion,
    lessonBusinessRationale: content.business_rationale,
    lessonGeneralizationBoundary: content.generalization_boundaries,
    lessonInvalidationCondition: content.invalidation_conditions,
    lessonHandlingGuidance: content.handling_guidance,
    lessonEditing: !!content.conclusion,
    lessonDraftProvenance: saved.last_generation
      ? `${saved.last_generation.provenance.model_name} / ${saved.last_generation.provenance.prompt_version}`
      : "共享编辑草稿",
    lessonDraftUncertainties: saved.last_generation?.uncertainties ?? [],
    sharedVersion: saved.version,
    sharedCandidateRevision: saved.candidate_revision,
    replacement: null,
  };
}
