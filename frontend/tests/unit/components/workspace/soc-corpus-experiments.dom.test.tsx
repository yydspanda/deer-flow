import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import {
  QueryClient,
  QueryClientProvider,
  QueryObserver,
} from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { SocCorpusExperiments } from "@/components/workspace/soc/soc-corpus-experiments";
import type { SocAnalysisExecutionOptions } from "@/core/soc/types";

const api = rs.hoisted(() => ({
  state: rs.fn(),
  run: rs.fn(),
  configuration: rs.fn(),
  concurrency: rs.fn(),
}));
rs.mock("@/core/soc/api", () => ({
  getSocCorpusQuickState: api.state,
  runSocCorpusQuick: api.run,
  getSocCorpusExperimentConfiguration: api.configuration,
  updateSocCorpusConcurrency: api.concurrency,
}));
const options: SocAnalysisExecutionOptions = {
  normalization_review_mode: "apply",
  tenant_policy_enabled: false,
  tenant_policy_advisor_enabled: false,
  tenant_policy_signal_providers_enabled: false,
};
const controls = {
  defaults: options,
  normalization_review_available: true,
  tenant_policy_available: true,
  tenant_policy_advisor_available: true,
  tenant_policy_signal_providers_available: true,
};
const SETTINGS_KEY = "soc.corpus.experiment.run-settings.v1";
beforeEach(() => {
  window.sessionStorage.clear();
  api.configuration.mockReset().mockResolvedValue({ defaults: options });
  api.concurrency.mockReset();
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
afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});
function mount(
  props: Partial<React.ComponentProps<typeof SocCorpusExperiments>> = {},
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  }),
) {
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

test("host saves shared concurrency during a batch without changing run switches or refetching audit", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  let capacity = 8;
  const configuration = () => ({
    defaults: options,
    can_configure: true,
    max_concurrency: capacity,
    concurrency_limit: 8,
  });
  api.configuration.mockImplementation(async () => configuration());
  let acknowledge: (value: unknown) => void = () => {
    throw new Error("Concurrency save has not started");
  };
  api.concurrency.mockImplementation(
    () =>
      new Promise((resolve) => {
        acknowledge = resolve;
      }),
  );
  api.state.mockResolvedValue({
    experiment_id: "EXP-test",
    total: 10,
    completed: 0,
    active: 8,
    remaining: 2,
    failed: 0,
    pending_candidates: 0,
    running: true,
    items: [],
  });
  client.setQueryData(
    ["soc-corpus-experiments", "configuration", "validation"],
    configuration(),
  );
  const queries = ["state", "activity", "audit"].map((kind) => {
    const queryFn = rs.fn().mockResolvedValue({ kind });
    const observer = new QueryObserver(client, {
      queryKey: ["soc-corpus-workbench", kind],
      queryFn,
      staleTime: Infinity,
    });
    return { queryFn, unsubscribe: observer.subscribe(rs.fn()) };
  });
  mount({ controls }, client);
  try {
    const selector = await screen.findByRole("combobox", { name: "最大并发" });
    expect((selector as HTMLSelectElement).value).toBe("8");
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["1", "2", "3", "4", "5", "6", "7", "8"]);
    await waitFor(() => expect(queries[1]!.queryFn).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("switch", { name: "企业策略" }));
    const savedSwitches = window.sessionStorage.getItem(SETTINGS_KEY);
    fireEvent.change(selector, { target: { value: "4" } });
    expect(api.concurrency).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "保存并发" }));
    await waitFor(() => expect(api.concurrency).toHaveBeenCalledWith(4));
    expect(screen.getByText("当前生效：8")).toBeTruthy();
    capacity = 4;
    await act(async () =>
      acknowledge({ max_concurrency: 4, concurrency_limit: 8 }),
    );
    await screen.findByText("当前生效：4");
    await waitFor(() => expect(queries[1]!.queryFn).toHaveBeenCalledTimes(3));
    expect(queries[0]!.queryFn).toHaveBeenCalledTimes(2);
    expect(queries[2]!.queryFn).toHaveBeenCalledTimes(1);
    expect(
      client.getQueryData([
        "soc-corpus-experiments",
        "configuration",
        "validation",
      ]),
    ).toMatchObject({ max_concurrency: 4 });
    expect(window.sessionStorage.getItem(SETTINGS_KEY)).toBe(savedSwitches);
    expect(api.run).not.toHaveBeenCalled();
    expect(
      screen
        .getByRole("switch", { name: "企业策略" })
        .getAttribute("aria-checked"),
    ).toBe("true");
    expect(screen.getByText(/降低后等待当前任务完成/)).toBeTruthy();
  } finally {
    for (const query of queries) query.unsubscribe();
    client.clear();
  }
});

test("a failed concurrency save keeps the acknowledged value and the draft for retry", async () => {
  api.configuration.mockResolvedValue({
    defaults: options,
    can_configure: true,
    max_concurrency: 8,
    concurrency_limit: 8,
  });
  api.concurrency.mockRejectedValueOnce(new Error("并发设置保存失败"));
  mount({ controls });
  const selector = await screen.findByRole("combobox", { name: "最大并发" });
  fireEvent.change(selector, { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并发" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "并发设置保存失败",
  );
  expect(screen.getByText("当前生效：8")).toBeTruthy();
  expect((selector as HTMLSelectElement).value).toBe("3");
  expect(
    screen.getByRole("button", { name: "保存并发" }).hasAttribute("disabled"),
  ).toBe(false);
  expect(api.concurrency).toHaveBeenCalledTimes(1);
  expect(api.run).not.toHaveBeenCalled();
});

test.each(["configuration", "controls"])(
  "LAN concurrency is read-only under %s permission",
  async (permission) => {
    api.configuration.mockResolvedValue({
      defaults: options,
      can_configure: permission !== "configuration",
      max_concurrency: 4,
      concurrency_limit: 8,
    });
    mount({
      controls: { ...controls, can_configure: permission !== "controls" },
    });
    expect(await screen.findByText("当前生效：4")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "最大并发" })).toBeNull();
    expect(screen.queryByRole("button", { name: "保存并发" })).toBeNull();
    expect(api.concurrency).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "开始积累" }));
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1));
  },
);

test("configuration refresh shares the acknowledged concurrency with another tab", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  api.configuration.mockResolvedValue({
    defaults: options,
    can_configure: false,
    max_concurrency: 8,
    concurrency_limit: 8,
  });
  mount({ controls }, client);
  await screen.findByText("当前生效：8");
  api.configuration.mockResolvedValue({
    defaults: options,
    can_configure: false,
    max_concurrency: 2,
    concurrency_limit: 8,
  });
  await act(async () => {
    await client.invalidateQueries({
      queryKey: ["soc-corpus-experiments", "configuration"],
    });
  });
  await screen.findByText("当前生效：2");
  expect(api.concurrency).not.toHaveBeenCalled();
  expect(api.run).not.toHaveBeenCalled();
  client.clear();
});
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

test("LAN settings ignore stored preferences and still permit batch start", async () => {
  const savedOptions = { ...options, tenant_policy_enabled: true };
  window.sessionStorage.setItem(SETTINGS_KEY, JSON.stringify(options));
  api.configuration.mockResolvedValue({
    defaults: options,
    saved_options: savedOptions,
    can_configure: false,
  });
  mount({ controls });
  const policy = await screen.findByRole("switch", { name: "企业策略" });
  expect(policy.getAttribute("aria-checked")).toBe("true");
  for (const input of screen.getAllByRole("switch")) {
    expect(input.hasAttribute("disabled")).toBe(true);
    fireEvent.click(input);
  }
  expect(
    screen
      .getByRole("checkbox", { name: "重新核对事实" })
      .hasAttribute("disabled"),
  ).toBe(true);
  const reset = screen.getByRole("button", { name: "恢复默认" });
  expect(reset.hasAttribute("disabled")).toBe(true);
  fireEvent.click(reset);
  fireEvent.click(screen.getByRole("button", { name: "开始积累" }));
  await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1));
  expect(api.run.mock.calls[0]![0]).toMatchObject({
    action: "start",
    options: savedOptions,
  });
  expect(api.configuration).toHaveBeenCalledWith("learning");
});

test.each(["pause", "rerun"])(
  "LAN users retain %s with the server-saved options",
  async (action) => {
    const savedOptions = { ...options, refresh_normalization: true };
    window.sessionStorage.setItem(SETTINGS_KEY, JSON.stringify(options));
    api.configuration.mockResolvedValue({
      defaults: options,
      saved_options: savedOptions,
      can_configure: false,
    });
    if (action === "pause") {
      api.state.mockResolvedValue({
        experiment_id: "EXP-test",
        total: 10,
        active: 1,
        completed: 0,
        remaining: 9,
        failed: 0,
        pending_candidates: 0,
        running: true,
        items: [],
      });
    }
    mount({
      controls,
      batch: "validation",
      ...(action === "rerun"
        ? {
            requestedAlert: {
              alertId: "A-4",
              key: 1,
              jobId: "JOB-old",
              rerun: true,
            },
          }
        : {}),
    });
    if (action === "pause") {
      const pause = await screen.findByRole("button", { name: "暂停" });
      await waitFor(() => expect(pause.hasAttribute("disabled")).toBe(false));
      fireEvent.click(pause);
    }
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1));
    expect(api.run.mock.calls[0]![0]).toMatchObject({
      batch: "validation",
      action,
      options: savedOptions,
    });
    expect(api.configuration).toHaveBeenCalledWith("validation");
  },
);

test.each([true, undefined])(
  "host and legacy configurations remain editable (can_configure: %s)",
  async (canConfigure) => {
    api.configuration.mockResolvedValue({
      defaults: options,
      can_configure: canConfigure,
    });
    mount({ controls });
    const policy = await screen.findByRole("switch", { name: "企业策略" });
    expect(policy.hasAttribute("disabled")).toBe(false);
    expect(screen.queryByRole("combobox", { name: "最大并发" })).toBeNull();
    fireEvent.click(policy);
    fireEvent.click(screen.getByRole("button", { name: "开始积累" }));
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1));
    expect(api.run.mock.calls[0]![0]).toMatchObject({
      action: "start",
      options: { ...options, tenant_policy_enabled: true },
    });
    expect(JSON.parse(window.sessionStorage.getItem(SETTINGS_KEY)!)).toEqual({
      ...options,
      tenant_policy_enabled: true,
    });
  },
);

test("a rejected remote command refreshes saved settings before an explicit retry", async () => {
  api.configuration.mockResolvedValue({
    defaults: options,
    can_configure: false,
    saved_options: options,
  });
  mount({ controls });
  await screen.findByRole("switch", { name: "企业策略" });
  const nextOptions = { ...options, tenant_policy_enabled: true };
  api.configuration.mockResolvedValue({
    defaults: options,
    can_configure: false,
    saved_options: nextOptions,
  });
  api.run.mockRejectedValueOnce(new Error("运行设置已更新"));
  fireEvent.click(screen.getByRole("button", { name: "开始积累" }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("switch", { name: "企业策略" })
        .getAttribute("aria-checked"),
    ).toBe("true"),
  );
  expect(api.run).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "开始积累" }));
  await waitFor(() => expect(api.run).toHaveBeenCalledTimes(2));
  expect(api.run.mock.calls[1]![0]).toMatchObject({ options: nextOptions });
});

test("batch progress refreshes an empty filtered list without refetching audits", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const queries = ["state", "activity", "execution", "audit"].map((kind) => {
    const queryFn = rs.fn().mockResolvedValue({ kind });
    const observer = new QueryObserver(client, {
      queryKey: ["soc-corpus-workbench", kind, "A-4", "RUN-completed"],
      queryFn,
      staleTime: Infinity,
    });
    return { queryFn, unsubscribe: observer.subscribe(rs.fn()) };
  });
  await waitFor(() => {
    for (const query of queries) expect(query.queryFn).toHaveBeenCalledTimes(1);
  });
  render(
    <QueryClientProvider client={client}>
      <SocCorpusExperiments
        batch="learning"
        tier="all"
        alertIds={[]}
        requestedAlert={null}
        onRequestHandled={rs.fn()}
      />
    </QueryClientProvider>,
  );
  try {
    await waitFor(() => {
      for (const query of queries.slice(0, 3))
        expect(query.queryFn).toHaveBeenCalledTimes(2);
    });
    expect(queries[3]!.queryFn).toHaveBeenCalledTimes(1);
    act(() => {
      client.setQueriesData(
        { queryKey: ["soc-corpus-quick"] },
        (old: unknown) => ({
          ...(old as Record<string, unknown>),
          completed: 1,
          remaining: 9,
          items: [],
        }),
      );
    });
    await waitFor(() => {
      for (const query of queries.slice(0, 3))
        expect(query.queryFn).toHaveBeenCalledTimes(3);
    });
    expect(queries[3]!.queryFn).toHaveBeenCalledTimes(1);
    expect(api.run).not.toHaveBeenCalled();

    // Starting another batch also refreshes only the lightweight workbench data.
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      for (const query of queries.slice(0, 3))
        expect(query.queryFn.mock.calls.length).toBeGreaterThan(3);
    });
    expect(queries[3]!.queryFn).toHaveBeenCalledTimes(1);
  } finally {
    for (const query of queries) query.unsubscribe();
    client.clear();
  }
});
