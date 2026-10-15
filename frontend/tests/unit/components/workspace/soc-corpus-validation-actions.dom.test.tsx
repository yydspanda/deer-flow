import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

import { SocCorpusValidationWorkbench } from "@/components/workspace/soc/soc-corpus-validation-workbench";
import type { SocAnalysisExecutionOptions } from "@/core/soc/types";

const api = rs.hoisted(() => ({
  state: rs.fn(),
  run: rs.fn(),
  configuration: rs.fn(),
  workbench: rs.fn(),
}));
rs.mock("@/core/soc/api", () => ({
  getSocCorpusQuickState: api.state,
  runSocCorpusQuick: api.run,
  getSocCorpusExperimentConfiguration: api.configuration,
}));
rs.mock("@/core/soc", () => ({
  useSocCorpusWorkbench: api.workbench,
  useSocCorpusWorkbenchActivity: () => ({ activity: null }),
  useSocCorpusWorkbenchExecution: () => ({ execution: null }),
  useProcessSocCorpusWorkbenchAlert: () => ({ isPending: false }),
  usePromoteSocRunToMemory: () => ({ isPending: false }),
}));
rs.mock("@/components/workspace/soc/soc-corpus-group-picker", () => ({
  SocCorpusGroupPicker: () => null,
}));
rs.mock("@/components/workspace/soc/soc-workspace-header", () => ({
  SocWorkspaceHeader: () => null,
}));

const options: SocAnalysisExecutionOptions = {
  normalization_review_mode: "apply",
  tenant_policy_enabled: false,
  tenant_policy_advisor_enabled: false,
  tenant_policy_signal_providers_enabled: false,
};

beforeEach(() => {
  window.sessionStorage.clear();
  api.run.mockReset().mockResolvedValue({ accepted: true });
  api.state.mockReset();
  api.configuration.mockReset().mockResolvedValue({ defaults: options });
  api.workbench.mockReset().mockReturnValue({
    state: {
      safety: { pattern_window_days: 180 },
      model: { model_name: "fixture-model" },
      source: { file_name: "fixture.pkl", sha256: "a".repeat(64) },
      batch_selection: {
        counts: {
          learning: 1,
          validation_main: 1,
          validation_supplementary: 0,
        },
        selected_count: 1,
        group_count: 1,
        labeled_count: 0,
        execution_enabled: false,
      },
      alert_page: { total: 1, limit: 20, offset: 0 },
      groups: [],
      source_types: ["hids"],
      alerts: [
        {
          alert_id: "A-4",
          sequence_number: 1,
          observed_at: "2026-10-01T00:00:00Z",
          source_type: "hids",
          rule_name: "fixture detection",
          group_id: "CG-test",
          group_alert_count: 5,
          window_alert_count: 5,
          readiness: "recurrent_strong",
          workflow_state: "failed",
          effective_label_comparison: "not_run",
          memory_contexts: [],
        },
      ],
    },
    refetch: rs.fn(),
  });
});
afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});

for (const batch of ["learning", "validation"] as const) {
  test.each(["job", "batch", null] as const)(
    `${batch} failed alert uses a new run only when blocked at %s level`,
    async (blockedAt) => {
      window.sessionStorage.setItem(
        "soc.corpus-validation.filters.v1",
        JSON.stringify({ batch }),
      );
      api.state.mockResolvedValue({
        experiment_id: "EXP-test",
        total: 1,
        completed: 0,
        active: 0,
        remaining: 0,
        failed: 1,
        pending_candidates: 0,
        running: false,
        blocked_reason: blockedAt === "batch" ? "configuration_changed" : null,
        items: [
          {
            alert_id: "A-4",
            job_id: "JOB-failed",
            status: "failed",
            attempt_count: 2,
            error_message: "fixture failure",
            blocked_reason:
              blockedAt === "job" ? "configuration_changed" : null,
          },
        ],
      });
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });
      render(
        <QueryClientProvider client={client}>
          <SocCorpusValidationWorkbench />
        </QueryClientProvider>,
      );
      await screen.findByText("fixture failure");
      const row = within(screen.getByText("A-4").closest("tr")!);
      const label = blockedAt ? "重新运行" : "重试";
      expect(
        row.queryByRole("button", { name: blockedAt ? "重试" : "重新运行" }),
      ).toBeNull();
      fireEvent.click(row.getByRole("button", { name: label }));
      await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1));
      expect(api.run.mock.calls[0]).toEqual(
        blockedAt
          ? [
              {
                batch,
                scope: "all",
                action: "rerun",
                alert_id: "A-4",
                options,
              },
              "quick-rerun:JOB-failed",
            ]
          : [
              {
                batch,
                scope: "all",
                action: "retry",
                alert_id: "A-4",
                job_id: "JOB-failed",
                expected_attempt_count: 2,
                options,
              },
              "quick-retry:JOB-failed:2",
            ],
      );
      client.clear();
    },
  );
}
