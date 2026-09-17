import { afterEach, beforeEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";

import { SocMemorySharedDraft } from "@/components/workspace/soc/soc-memory-shared-draft";
import {
  defaultMemoryCandidateReviewDraft,
  type MemoryCandidateReviewDraft,
} from "@/core/soc/memory-review-draft";
import {
  workingDraftContent,
  workingDraftPatch,
  type SocMemoryWorkingDraft,
  type SocMemoryWorkingDraftView,
} from "@/core/soc/memory-working-draft";
import type { SocMemoryCandidate } from "@/core/soc/types";

const api = rs.hoisted(() => ({ read: rs.fn(), save: rs.fn() }));
rs.mock("@/core/soc/api", () => ({
  getSocMemoryWorkingDraft: api.read,
  saveSocMemoryWorkingDraft: api.save,
}));
const candidate = { candidate_id: "MC-ONE" } as SocMemoryCandidate;
const revision = "a".repeat(64);
const defaults = () => defaultMemoryCandidateReviewDraft(candidate);
const saved = (version = 1): SocMemoryWorkingDraft => ({
  candidate_id: candidate.candidate_id,
  candidate_revision: revision,
  version,
  content: workingDraftContent({
    ...defaults(),
    confirmedVerdict: "false_positive",
    lessonConclusion: "共享结论",
  }),
  updated_by: "reviewer",
  updated_at: "2026-09-18T01:00:00Z",
  authority: "draft_only",
  generation_job_id: null,
  last_generation: null,
});
const view = (
  draft: SocMemoryWorkingDraft | null = null,
): SocMemoryWorkingDraftView => ({
  candidate_id: candidate.candidate_id,
  candidate_revision: revision,
  editable: true,
  stale: false,
  draft,
});

function Editor({ initial }: { initial?: MemoryCandidateReviewDraft }) {
  const [draft, setDraft] = useState(initial ?? defaults());
  const [hasLocalDraft, setLocal] = useState(!!initial);
  return (
    <>
      <input
        aria-label="审核结论草稿"
        value={draft.lessonConclusion}
        onChange={(e) => {
          setLocal(true);
          setDraft({ ...draft, lessonConclusion: e.target.value });
        }}
      />
      <output data-testid="version">{draft.sharedVersion}</output>
      <SocMemorySharedDraft
        candidateId={candidate.candidate_id}
        current={draft}
        hasLocalDraft={hasLocalDraft}
        busy={false}
        onChange={(patch) => {
          setLocal(true);
          setDraft((previous) => ({ ...previous, ...patch }));
        }}
      />
    </>
  );
}
function mount(initial?: MemoryCandidateReviewDraft) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <Editor initial={initial} />
    </QueryClientProvider>,
  );
  return client;
}
beforeEach(() => {
  api.read.mockReset();
  api.save.mockReset();
  api.read.mockResolvedValue(view());
});
afterEach(() => cleanup());

it("restores shared content only when this browser has no unsaved draft", async () => {
  api.read.mockResolvedValue(view(saved()));
  mount();
  await waitFor(() =>
    expect(screen.getByLabelText<HTMLInputElement>("审核结论草稿").value).toBe(
      "共享结论",
    ),
  );
  expect(screen.getByTestId("version").textContent).toBe("1");
  expect(api.save).not.toHaveBeenCalled();
});

it("does not overwrite an unsent browser draft when a shared version exists", async () => {
  api.read.mockResolvedValue(view(saved()));
  mount({ ...defaults(), lessonConclusion: "本页未保存" });
  await screen.findByRole("button", { name: "读取共享草稿" });
  expect(screen.getByLabelText<HTMLInputElement>("审核结论草稿").value).toBe(
    "本页未保存",
  );
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "保存草稿" })
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "读取共享草稿" }));
  fireEvent.click(await screen.findByRole("button", { name: /^确认$/ }));
  await waitFor(() =>
    expect(screen.getByLabelText<HTMLInputElement>("审核结论草稿").value).toBe(
      "共享结论",
    ),
  );
});

it("save acknowledgement advances the base version without replacing in-flight edits", async () => {
  let complete!: (result: SocMemoryWorkingDraft) => void;
  api.save.mockReturnValue(
    new Promise<SocMemoryWorkingDraft>((resolve) => {
      complete = resolve;
    }),
  );
  mount();
  await waitFor(() =>
    expect(screen.getByTestId("version").textContent).toBe("0"),
  );
  fireEvent.change(screen.getByLabelText("审核结论草稿"), {
    target: { value: "保存时文字" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1));
  expect(api.save.mock.calls[0]?.[1]).toMatchObject({
    expected_version: 0,
    candidate_revision: revision,
    content: {
      conclusion: "保存时文字",
      reviewer_verdict: null,
      apply_to_future_matches: false,
    },
  });
  fireEvent.change(screen.getByLabelText("审核结论草稿"), {
    target: { value: "发送中继续修改" },
  });
  await act(async () => complete(saved()));
  await waitFor(() =>
    expect(screen.getByTestId("version").textContent).toBe("1"),
  );
  expect(screen.getByLabelText<HTMLInputElement>("审核结论草稿").value).toBe(
    "发送中继续修改",
  );
});

it("a conflicting save retains text and never silently rebases on the latest server version", async () => {
  api.read.mockResolvedValue(view(saved()));
  api.save.mockRejectedValue(new Error("草稿版本冲突"));
  mount({
    ...defaults(),
    ...workingDraftPatch(saved()),
    lessonConclusion: "我的修改",
  });
  await screen.findByRole("button", { name: "读取共享草稿" });
  api.read.mockResolvedValue(view(saved(2)));
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await screen.findByRole("alert");
  await screen.findByText(/共享草稿已更新/);
  expect(screen.getByTestId("version").textContent).toBe("1");
  expect(screen.getByLabelText<HTMLInputElement>("审核结论草稿").value).toBe(
    "我的修改",
  );
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "保存草稿" })
      .disabled,
  ).toBe(true);
});

it("retains all seven editable fields and usage intent in a server round trip", () => {
  const draft = {
    ...defaults(),
    confirmedVerdict: "true_positive" as const,
    businessContext: "",
    applyToFutureMatches: true,
    selectedBehaviorComponents: ["network_service:udp/1194"],
    promotedFacetValues: { role_entity: ["destination:192.0.2.1"] },
    lessonDetectionScenario: "规则报告",
    lessonObservedEvent: "实际事件",
    lessonConclusion: "审核结论",
    lessonBusinessRationale: "依据1\n依据2",
    lessonGeneralizationBoundary: "泛化边界",
    lessonInvalidationCondition: "失效条件",
    lessonHandlingGuidance: "处理建议",
  };
  const restored = {
    ...defaults(),
    ...workingDraftPatch({ ...saved(), content: workingDraftContent(draft) }),
  };
  expect(workingDraftContent(restored)).toEqual(workingDraftContent(draft));
  expect(restored.replacement).toBeNull();
});
