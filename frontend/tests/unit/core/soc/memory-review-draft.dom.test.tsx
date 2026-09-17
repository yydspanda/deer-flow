import { afterEach, beforeEach, expect, it, rs } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useMemoryReviewDrafts } from "@/core/soc/memory-review-draft";
import type { SocMemoryCandidate } from "@/core/soc/types";

const auth = rs.hoisted(() => ({ user: { id: "analyst-a" } }));
rs.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => auth }));

const candidate = {
  candidate_id: "MC-A",
  tenant_id: "tenant-a",
  status: "pending_review",
  created_at: "2026-09-17T00:00:00Z",
  updated_at: "2026-09-17T00:00:00Z",
} as SocMemoryCandidate;
const candidates = [candidate];
const patch = {
  confirmedVerdict: "true_positive" as const,
  businessContext: "Reviewer supplied facts",
  lessonConclusion: "Reviewed draft conclusion",
  selectedBehaviorComponents: ["network_service:udp/1194"],
  promotedFacetValues: { role_entity: ["destination:192.0.2.10"] },
  applyToFutureMatches: true,
};

beforeEach(() => {
  auth.user = { id: "analyst-a" };
  window.sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
});

it("restores all inputs after remount, including edits and matching choices", () => {
  const first = renderHook(() => useMemoryReviewDrafts(candidates));
  act(() => first.result.current.change(candidate, patch));
  act(() =>
    first.result.current.change(candidate, {
      lessonObservedEvent: "edited event",
    }),
  );
  first.unmount();
  const { result } = renderHook(() => useMemoryReviewDrafts(candidates));
  expect(result.current.drafts["MC-A"]).toMatchObject({
    ...patch,
    lessonObservedEvent: "edited event",
  });
});

it("isolates drafts by candidate, tenant and reviewer", () => {
  const { result, rerender } = renderHook(useMemoryReviewDrafts, {
    initialProps: candidates,
  });
  act(() => result.current.change(candidate, patch));
  rerender([{ ...candidate, candidate_id: "MC-B" }]);
  expect(result.current.drafts).toEqual({});
  rerender([{ ...candidate, tenant_id: "tenant-b" }]);
  expect(result.current.drafts).toEqual({});
  auth.user = { id: "analyst-b" };
  rerender(candidates);
  expect(result.current.drafts).toEqual({});
  auth.user = { id: "analyst-a" };
  rerender([...candidates]);
  expect(result.current.drafts["MC-A"]).toMatchObject(patch);
});

it("discards a draft after the authoritative candidate changes", () => {
  const { result, rerender } = renderHook(useMemoryReviewDrafts, {
    initialProps: candidates,
  });
  act(() => result.current.change(candidate, patch));
  rerender([{ ...candidate, updated_at: "2026-09-17T01:00:00Z" }]);
  expect(result.current.drafts).toEqual({});
  expect(window.sessionStorage.length).toBe(0);
});

it("clears completed or rejected candidate drafts, including on another reviewer's update", () => {
  const { result, rerender } = renderHook(useMemoryReviewDrafts, {
    initialProps: candidates,
  });
  act(() => result.current.change(candidate, patch));
  act(() => result.current.clear(candidate));
  expect(result.current.drafts).toEqual({});
  expect(window.sessionStorage.length).toBe(0);
  act(() => result.current.change(candidate, patch));
  rerender([{ ...candidate, status: "confirmed" }]);
  expect(result.current.drafts).toEqual({});
  expect(window.sessionStorage.length).toBe(0);
});

it.each(["corrupt", "expired", "wrong-shape"])(
  "ignores %s stored data",
  (mode) => {
    const first = renderHook(() => useMemoryReviewDrafts(candidates));
    act(() => first.result.current.change(candidate, patch));
    const key = window.sessionStorage.key(0)!;
    const saved = JSON.parse(window.sessionStorage.getItem(key)!);
    first.unmount();
    window.sessionStorage.setItem(
      key,
      mode === "corrupt"
        ? "{"
        : JSON.stringify({
            ...saved,
            ...(mode === "expired"
              ? { savedAt: Date.now() - 25 * 60 * 60 * 1000 }
              : { draft: {} }),
          }),
    );
    const { result } = renderHook(() => useMemoryReviewDrafts(candidates));
    expect(result.current.drafts).toEqual({});
    expect(window.sessionStorage.length).toBe(0);
  },
);

it("keeps current edits usable and reports unavailable browser storage", () => {
  const { result } = renderHook(() => useMemoryReviewDrafts(candidates));
  rs.spyOn(window.sessionStorage, "setItem").mockImplementation(() => {
    throw new Error("quota");
  });
  act(() => result.current.change(candidate, patch));
  expect(result.current.drafts["MC-A"]).toMatchObject(patch);
  expect(result.current.storageFailed).toBe(true);
});
