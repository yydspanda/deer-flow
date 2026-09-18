import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { SocCorpusExperiments } from "@/components/workspace/soc/soc-corpus-experiments";

const api = rs.hoisted(() => ({
  state: rs.fn(),
  run: rs.fn(),
  configuration: rs.fn(),
}));
rs.mock("@/core/soc/api", () => ({
  getSocCorpusQuickState: api.state,
  runSocCorpusQuick: api.run,
  getSocCorpusExperimentConfiguration: api.configuration,
}));
const options = {
  normalization_review_mode: "apply",
  tenant_policy_enabled: false,
  tenant_policy_advisor_enabled: false,
  tenant_policy_signal_providers_enabled: false,
};
beforeEach(() => {
  api.configuration.mockReset().mockResolvedValue({ defaults: options });
  api.run.mockReset().mockResolvedValue({ accepted: true });
  api.state.mockReset().mockResolvedValue({
    experiment_id: "EXP-test",
    total: 10,
    completed: 0,
    active: 0,
    remaining: 10,
    failed: 0,
    pending_candidates: 2,
    running: false,
    items: [],
  });
});
afterEach(cleanup);
function mount(
  props: Partial<React.ComponentProps<typeof SocCorpusExperiments>> = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SocCorpusExperiments
        batch="learning"
        tier="all"
        groupId="filtered-group"
        requestedAlert={null}
        onRequestHandled={rs.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
}
test("full learning starts in one click without group filters or preparation", async () => {
  mount();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "开始积累" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "开始积累" }));
  await waitFor(() =>
    expect(api.run).toHaveBeenCalledWith(
      {
        batch: "learning",
        scope: "all",
        action: "start",
        alert_id: undefined,
        options,
      },
      undefined,
    ),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByText("待审核经验 2")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "审核经验" }).getAttribute("href"),
  ).toContain("experiment=EXP-test&return_batch=learning");
});
test("single alert runs directly while batch is paused", async () => {
  const handled = rs.fn();
  mount({
    requestedAlert: { alertId: "A-4", key: 1 },
    onRequestHandled: handled,
  });
  await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1));
  expect(api.run.mock.calls[0]![0]).toMatchObject({
    action: "run",
    alert_id: "A-4",
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  await waitFor(() => expect(handled).toHaveBeenCalled());
});
test("second-batch scope is explicit and rerun uses the old task as retry identity", async () => {
  mount({
    batch: "validation",
    tier: "supplementary",
    requestedAlert: { alertId: "A-4", key: 1, jobId: "JOB-old", rerun: true },
  });
  await waitFor(() =>
    expect(api.run).toHaveBeenCalledWith(
      {
        batch: "validation",
        scope: "explore",
        action: "rerun",
        alert_id: "A-4",
        options,
      },
      "quick-rerun:JOB-old",
    ),
  );
});
