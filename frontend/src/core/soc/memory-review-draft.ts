"use client";

import { useEffect, useRef, useState } from "react";
import { z } from "zod";

import { useAuth } from "@/core/auth/AuthProvider";

import type { SocMemoryCandidate } from "./types";

const draftSchema = z.object({
  businessContext: z.string(),
  applyToFutureMatches: z.boolean(),
  confirmedVerdict: z
    .enum([
      "true_positive",
      "false_positive",
      "suspicious",
      "unknown",
      "needs_review",
    ])
    .nullable(),
  promotedFacetValues: z.record(z.array(z.string())),
  selectedBehaviorComponents: z.array(z.string()).nullable(),
  lessonDetectionScenario: z.string(),
  lessonObservedEvent: z.string(),
  lessonConclusion: z.string(),
  lessonBusinessRationale: z.string(),
  lessonGeneralizationBoundary: z.string(),
  lessonInvalidationCondition: z.string(),
  lessonHandlingGuidance: z.string(),
  lessonDraftProvenance: z.string(),
  lessonDraftUncertainties: z.array(z.string()),
  lessonEditing: z.boolean(),
  replacement: z
    .object({ memoryId: z.string(), version: z.number().int() })
    .nullable(),
});
const savedSchema = z.object({
  version: z.literal(1),
  revision: z.string(),
  savedAt: z.number(),
  draft: draftSchema,
});
export type MemoryCandidateReviewDraft = z.infer<typeof draftSchema>;
type SavedDraft = z.infer<typeof savedSchema>;
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export function defaultMemoryCandidateReviewDraft(
  candidate: SocMemoryCandidate,
): MemoryCandidateReviewDraft {
  return {
    businessContext: "",
    applyToFutureMatches: false,
    confirmedVerdict: null,
    promotedFacetValues: {},
    selectedBehaviorComponents:
      candidate.applicability?.selected_behavior_components ??
      candidate.scope_view?.required_details.behavior_fingerprint
        ?.behavior_component ??
      null,
    lessonDetectionScenario: "",
    lessonObservedEvent: "",
    lessonConclusion: "",
    lessonBusinessRationale: "",
    lessonGeneralizationBoundary: "",
    lessonInvalidationCondition: "",
    lessonHandlingGuidance: "",
    lessonDraftProvenance: "",
    lessonDraftUncertainties: [],
    lessonEditing: false,
    replacement: null,
  };
}

function editable(candidate: SocMemoryCandidate) {
  return ["pending_review", "confirmed_candidate"].includes(candidate.status);
}

function revision(candidate: SocMemoryCandidate) {
  return JSON.stringify([candidate.created_at, candidate.updated_at]);
}

// This is an unsent form cache, never a Memory record or an approval.
export function useMemoryReviewDrafts(candidates: SocMemoryCandidate[]) {
  const { user } = useAuth();
  const entries = useRef<Record<string, SavedDraft>>({});
  const [snapshot, setSnapshot] = useState(entries.current);
  const [storageFailed, setStorageFailed] = useState(false);
  const userId = user?.id;
  const keyFor = (candidate: SocMemoryCandidate) =>
    `soc-memory-review-draft:v1:${JSON.stringify([userId, candidate.tenant_id, candidate.candidate_id])}`;

  useEffect(() => {
    if (!userId) return;
    let changed = false;
    for (const candidate of candidates) {
      const key = `soc-memory-review-draft:v1:${JSON.stringify([userId, candidate.tenant_id, candidate.candidate_id])}`;
      const current = entries.current[key];
      if (
        current &&
        editable(candidate) &&
        current.revision === revision(candidate)
      )
        continue;
      try {
        const raw = window.sessionStorage.getItem(key);
        let saved: SavedDraft | undefined;
        if (raw && raw.length <= 500_000) {
          try {
            const parsed = savedSchema.safeParse(JSON.parse(raw));
            if (parsed.success) saved = parsed.data;
          } catch {
            /* Ignore incomplete or obsolete browser drafts. */
          }
        }
        if (
          saved &&
          editable(candidate) &&
          saved.revision === revision(candidate) &&
          Date.now() - saved.savedAt < MAX_AGE_MS &&
          saved.savedAt <= Date.now()
        ) {
          entries.current = { ...entries.current, [key]: saved };
          changed = true;
        } else {
          window.sessionStorage.removeItem(key);
          if (current) {
            const next = { ...entries.current };
            delete next[key];
            entries.current = next;
            changed = true;
          }
        }
      } catch {
        setStorageFailed(true);
      }
    }
    if (changed) setSnapshot(entries.current);
  }, [candidates, userId]);

  function change(
    candidate: SocMemoryCandidate,
    patch: Partial<MemoryCandidateReviewDraft>,
  ) {
    if (!userId || !editable(candidate)) return;
    const key = keyFor(candidate);
    const current = entries.current[key];
    const draft =
      current?.revision === revision(candidate)
        ? current.draft
        : defaultMemoryCandidateReviewDraft(candidate);
    const saved: SavedDraft = {
      version: 1,
      revision: revision(candidate),
      savedAt: Date.now(),
      draft: {
        ...draft,
        ...patch,
        ...(patch.promotedFacetValues || patch.selectedBehaviorComponents
          ? { replacement: null }
          : {}),
      },
    };
    entries.current = { ...entries.current, [key]: saved };
    setSnapshot(entries.current);
    // Write in the input/generation callback so immediate navigation cannot lose the edit.
    try {
      window.sessionStorage.setItem(key, JSON.stringify(saved));
    } catch {
      setStorageFailed(true);
    }
  }

  function clear(candidate: SocMemoryCandidate) {
    const key = keyFor(candidate);
    const next = { ...entries.current };
    delete next[key];
    entries.current = next;
    setSnapshot(next);
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      setStorageFailed(true);
    }
  }

  const drafts: Record<string, MemoryCandidateReviewDraft> = {};
  for (const candidate of candidates) {
    const saved = snapshot[keyFor(candidate)];
    if (
      userId &&
      saved &&
      editable(candidate) &&
      saved.revision === revision(candidate)
    ) {
      drafts[candidate.candidate_id] = saved.draft;
    }
  }
  return { drafts, change, clear, storageFailed };
}
