import { afterEach, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { SocMemoryLearningStatus } from "@/components/workspace/soc/soc-memory-learning-status";
import { fetch as fetcher } from "@/core/api/fetcher";
import {
  socAlertQueryKeys,
  socCorpusWorkbenchQueryKeys,
  usePromoteSocRunToMemory,
  useReviewSocMemoryCandidate,
  useSocAlertInvestigationContext,
  useSocCorpusWorkbench,
} from "@/core/soc/hooks";
import type {
  SocAlertInvestigationContext,
  SocCorpusWorkbenchState,
  SocMemoryLearningView,
} from "@/core/soc/types";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "reviewer" } }),
}));

const pending: SocMemoryLearningView = {
  state: "pending_review",
  label: "人工提炼经验待审核",
  detail: "人工提炼已保存。",
  action: "review",
  action_label: "审核人工提炼经验",
  candidate_id: "MC-manual",
};
const accumulating: SocMemoryLearningView = {
  state: "accumulating",
  label: "正在积累",
  detail: "等待更多样本。",
  action: "promote",
  action_label: "提炼经验",
};
const stateKey = socCorpusWorkbenchQueryKeys.state({});
const auditKey = socCorpusWorkbenchQueryKeys.audit("A-1", "RUN-1");
const contextKey = socAlertQueryKeys.context("RUN-1");
const receipt = {
  run_id: "RUN-1",
  alert_id: "A-1",
  memory_candidate: { candidate_id: "MC-manual", status: "pending_review" },
  learning: pending,
};

function state(runId = "RUN-1", alertId = "A-1") {
  return {
    alerts: [{ alert_id: alertId, run_id: runId, learning: accumulating }],
  } as SocCorpusWorkbenchState;
}

function setup() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

afterEach(() => {
  cleanup();
  rs.mocked(fetcher).mockReset();
});

test("a saved promotion retains its review link when both result refreshes fail", async () => {
  const { client, wrapper } = setup();
  client.setQueryData(stateKey, state());
  client.setQueryData(contextKey, {
    run: { run_id: "RUN-1", alert_id: "A-1" },
    learning: accumulating,
  } as SocAlertInvestigationContext);
  rs.mocked(fetcher).mockImplementation(async (_url, init) => {
    if (init?.method === "POST") return Response.json(receipt);
    throw new Error("final read unavailable");
  });
  const { result } = renderHook(
    () => ({
      mutation: usePromoteSocRunToMemory(),
      corpus: useSocCorpusWorkbench(),
      context: useSocAlertInvestigationContext("RUN-1"),
    }),
    { wrapper },
  );
  await act(async () => {
    await result.current.mutation.mutateAsync({ runId: "RUN-1", request: {} });
  });
  await waitFor(() => {
    expect(result.current.corpus.isError).toBe(true);
    expect(result.current.context.isError).toBe(true);
  });
  expect(result.current.corpus.state?.alerts[0]?.learning).toEqual(pending);
  expect(result.current.context.context?.learning).toEqual(pending);
  const html = renderToStaticMarkup(
    <SocMemoryLearningStatus
      view={result.current.corpus.state!.alerts[0]!.learning!}
    />,
  );
  expect(html).toContain("/workspace/soc/review/memory-candidates/MC-manual");
  client.clear();
});

test("promotion fences an older list read and updates only its alert and Run", async () => {
  const { client, wrapper } = setup();
  const oldState = state();
  const otherRunKey = socCorpusWorkbenchQueryKeys.state({
    search: "other-run",
  });
  const otherAlertKey = socCorpusWorkbenchQueryKeys.state({
    search: "other-alert",
  });
  const otherRun = state("RUN-2");
  const otherAlert = state("RUN-1", "A-2");
  const audit = { learning: accumulating };
  client.setQueryData(stateKey, oldState);
  client.setQueryData(otherRunKey, otherRun);
  client.setQueryData(otherAlertKey, otherAlert);
  client.setQueryData(auditKey, audit);
  let resolveOldRead!: (value: SocCorpusWorkbenchState) => void;
  const oldRead = client
    .fetchQuery({
      queryKey: stateKey,
      queryFn: () =>
        new Promise<SocCorpusWorkbenchState>((resolve) => {
          resolveOldRead = resolve;
        }),
    })
    .catch(() => undefined);
  rs.mocked(fetcher).mockResolvedValue(Response.json(receipt));
  const { result } = renderHook(usePromoteSocRunToMemory, { wrapper });
  await act(async () => {
    await result.current.mutateAsync({ runId: "RUN-1", request: {} });
    resolveOldRead(oldState);
    await oldRead;
  });
  expect(
    client.getQueryData<SocCorpusWorkbenchState>(stateKey)?.alerts[0]?.learning,
  ).toEqual(pending);
  expect(client.getQueryData(otherRunKey)).toEqual(otherRun);
  expect(client.getQueryData(otherAlertKey)).toEqual(otherAlert);
  expect(client.getQueryData(auditKey)).toEqual(audit);
  expect(client.getQueryState(auditKey)?.isInvalidated).toBe(false);
  client.clear();
});

test("a promotion without a server learning view never invents a review destination", async () => {
  const { client, wrapper } = setup();
  client.setQueryData(stateKey, state());
  rs.mocked(fetcher).mockResolvedValue(
    Response.json({ ...receipt, learning: null }),
  );
  const { result } = renderHook(usePromoteSocRunToMemory, { wrapper });
  await act(async () => {
    await result.current.mutateAsync({ runId: "RUN-1", request: {} });
  });
  expect(
    client.getQueryData<SocCorpusWorkbenchState>(stateKey)?.alerts[0]?.learning,
  ).toEqual(accumulating);
  client.clear();
});

test("review fences an old list read and refreshes recent corpus and alert caches on return", async () => {
  const { client, wrapper } = setup();
  client.setQueryData(stateKey, state());
  client.setQueryData(contextKey, { learning: pending });
  client.setQueryData(auditKey, { learning: pending });
  let resolveOldRead!: (value: SocCorpusWorkbenchState) => void;
  const oldRead = client
    .fetchQuery({
      queryKey: stateKey,
      queryFn: () =>
        new Promise<SocCorpusWorkbenchState>((resolve) => {
          resolveOldRead = resolve;
        }),
    })
    .catch(() => undefined);
  const reviewed: SocMemoryLearningView = {
    state: "confirmed",
    label: "经验已保存",
    detail: "仅供研判参考。",
    action: "view_memory",
    action_label: "查看经验",
    memory_id: "MEM-1",
  };
  const refreshed = {
    ...state(),
    alerts: [{ ...state().alerts[0], learning: reviewed }],
  };
  rs.mocked(fetcher).mockImplementation(async (_url, init) =>
    Response.json(
      init?.method === "POST"
        ? { candidate: receipt.memory_candidate }
        : refreshed,
    ),
  );
  const { result } = renderHook(useReviewSocMemoryCandidate, { wrapper });
  await act(async () => {
    await result.current.mutateAsync({
      candidateId: "MC-manual",
      request: { decision: "confirm", reason: "审核通过" },
    });
    resolveOldRead(state());
    await oldRead;
  });
  expect(client.getQueryState(stateKey)?.isInvalidated).toBe(true);
  expect(client.getQueryState(contextKey)?.isInvalidated).toBe(true);
  expect(client.getQueryState(auditKey)?.isInvalidated).toBe(false);
  const returned = renderHook(useSocCorpusWorkbench, { wrapper });
  await waitFor(() => {
    expect(returned.result.current.state?.alerts[0]?.learning).toEqual(
      reviewed,
    );
  });
  client.clear();
});
