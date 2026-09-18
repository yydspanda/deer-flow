import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

import { SocCorpusExperiments } from "@/components/workspace/soc/soc-corpus-experiments";
import type {
  SocCorpusRoundProgress,
  SocCorpusRoundResults,
} from "@/core/soc/corpus-experiments";

const api = rs.hoisted(() => ({
  progress: rs.fn(),
  results: rs.fn(),
  experiments: rs.fn(),
  rounds: rs.fn(),
  configuration: rs.fn(),
}));
rs.mock("@/core/soc/api", () => ({
  getSocCorpusRound: api.progress,
  getSocCorpusRoundResults: api.results,
  getSocCorpusExperiments: api.experiments,
  getSocCorpusRounds: api.rounds,
  getSocCorpusExperimentConfiguration: api.configuration,
  createSocCorpusRound: rs.fn(),
  pauseSocCorpusRound: rs.fn(),
  prepareSocCorpusExperiment: rs.fn(),
  retrySocCorpusRound: rs.fn(),
  startSocCorpusRound: rs.fn(),
}));
rs.mock("@/components/workspace/soc/soc-corpus-round-comparison", () => ({
  SocCorpusRoundComparison: () => null,
}));

const progress: SocCorpusRoundProgress = {
  round: {
    round_id: "ROUND-1",
    experiment_id: "EXP-1",
    state: "running",
    version: 1,
    selection: {
      batch: "learning",
      scope: "reuse",
      group_ids: [],
      rule_codes: [],
      alert_ids: [],
    },
    purpose: "memory",
    memory_mode: "none",
    memory_snapshot: [],
    execution_limit: 1,
    concurrency: 1,
    created_at: "2026-09-18T00:00:00Z",
    config_hash: "hash",
    options: {
      normalization_review_mode: "apply",
      tenant_policy_enabled: false,
      tenant_policy_advisor_enabled: false,
      tenant_policy_signal_providers_enabled: false,
    },
  },
  selected_count: 1,
  admitted_count: 1,
  completed_count: 0,
  failed_count: 0,
  active_count: 1,
  counts: { analyzing: 1 },
};
const results: SocCorpusRoundResults = {
  round_id: "ROUND-1",
  total: 1,
  items: [
    {
      alert_id: "ALERT-1",
      group_id: "G-1",
      run_id: null,
      status: "analyzing",
      candidate_id: null,
      error_message: null,
      snapshot_changed_during_run: false,
      summary: {},
      label: {},
    },
  ],
};
const completed: SocCorpusRoundResults = {
  ...results,
  items: [
    {
      ...results.items[0]!,
      status: "completed",
      run_id: "RUN-1",
      summary: { recommended_handling: "ignore" },
    },
  ],
};
beforeEach(() => {
  api.progress.mockReset().mockResolvedValue(progress);
  api.results.mockReset().mockResolvedValue(results);
  api.experiments.mockReset().mockResolvedValue([
    {
      experiment_id: "EXP-1",
      name: "test",
      member_count: 1,
      plan_id: "PLAN-1",
      created_at: "2026-09-18T00:00:00Z",
    },
  ]);
  api.rounds.mockReset().mockResolvedValue([
    {
      round_id: "ROUND-1",
      batch: "learning",
      state: "running",
      created_at: "2026-09-18T00:00:00Z",
    },
  ]);
  api.configuration.mockReset().mockResolvedValue({
    defaults: progress.round.options,
    max_concurrency: 3,
  });
  sessionStorage.setItem(
    "soc.corpus.experiment",
    JSON.stringify({ id: "EXP-1" }),
  );
  sessionStorage.setItem("soc.corpus.round:EXP-1:learning", "ROUND-1");
});
afterEach(() => {
  cleanup();
  sessionStorage.clear();
});

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={client}>
      <SocCorpusExperiments
        batch="learning"
        tier="main"
        groupId="all"
        requestedAlert={null}
        onRequestHandled={() => undefined}
      />
    </QueryClientProvider>,
  );
  return client;
}
async function finish(client: QueryClient) {
  api.progress.mockResolvedValue({
    ...progress,
    round: { ...progress.round, state: "completed", version: 2 },
    active_count: 0,
    completed_count: 1,
    counts: { completed: 1 },
  });
  await act(async () => {
    await client.invalidateQueries({
      queryKey: ["soc-corpus-experiments", "round", "ROUND-1"],
    });
  });
}

test("terminal progress refreshes results after an earlier analyzing response", async () => {
  const client = mount();
  await screen.findByText("研判中");
  api.results.mockResolvedValue(completed);
  await finish(client);
  await screen.findByText("忽略");
  expect(screen.getByRole("button", { name: "本轮结果" })).toBeTruthy();
  const acknowledgedReads = api.results.mock.calls.length;
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3_200));
  });
  expect(api.results.mock.calls.length).toBe(acknowledgedReads);
  client.clear();
});

test("a failed final result fetch keeps polling until acknowledgement", async () => {
  const client = mount();
  await screen.findByText("研判中");
  api.results
    .mockRejectedValueOnce(new Error("temporary results error"))
    .mockResolvedValue(completed);
  await finish(client);
  await waitFor(() => expect(screen.getByText("忽略")).toBeTruthy(), {
    timeout: 5_000,
  });
  expect(screen.queryByText("研判中")).toBeNull();
  client.clear();
});

test("a late running-results response cannot overwrite the final result", async () => {
  const client = mount();
  await screen.findByText("研判中");
  let release!: (value: SocCorpusRoundResults) => void;
  api.results.mockImplementationOnce(
    () =>
      new Promise<SocCorpusRoundResults>((resolve) => {
        release = resolve;
      }),
  );
  let staleRead!: Promise<void>;
  act(() => {
    staleRead = client.invalidateQueries({
      queryKey: ["soc-corpus-experiments", "results", "ROUND-1"],
    });
  });
  await waitFor(() => expect(release).toBeDefined());
  api.results.mockResolvedValue(completed);
  await finish(client);
  await screen.findByText("忽略");
  await act(async () => {
    release(results);
    await staleRead;
  });
  expect(screen.getByRole("button", { name: "本轮结果" })).toBeTruthy();
  expect(screen.queryByText("研判中")).toBeNull();
  client.clear();
});
