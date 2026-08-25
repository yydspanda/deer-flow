import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const ALERT_IDS = [
  "1984426",
  "1984281",
  "1984525",
  "1984510",
  "1984659",
  "1984919",
  "1966874",
  "1966879",
  "1967880",
  "1974113",
  "1980607",
  "1980502",
  "1980722",
  "1982981",
];

function alert(index: number, processed: boolean) {
  const phase =
    index < 5 ? "construction" : index === 5 ? "held_out" : "additional";
  return {
    alert_id: ALERT_IDS[index],
    phase,
    phase_order:
      phase === "construction"
        ? index + 1
        : phase === "held_out"
          ? 1
          : index - 5,
    observed_at: `2026-04-27T18:${String(4 + index).padStart(2, "0")}:42+08:00`,
    endpoint: `10.28.20.${80 + index}`,
    host_name: `GALAXY-${index + 1}`,
    process_names: ["services.exe", "svchost.exe"],
    workflow_state: processed ? "completed" : index === 0 ? "ready" : "locked",
    can_process: !processed && index === 0,
    run_id: processed ? "RUN-MEMORY-DEV-001" : null,
    analysis_status: processed ? "needs_review" : null,
    model_name: processed ? "fixture-model" : null,
    prompt_version: processed ? "soc-analysis-v35" : null,
    total_duration_ms: processed ? 1240 : null,
    output_quality: processed ? "accepted" : null,
    base_verdict: processed ? "suspicious" : null,
    base_confidence: processed ? 0.78 : null,
    base_needs_review: processed ? true : null,
    effective_verdict: processed ? "suspicious" : null,
    effective_confidence: processed ? 0.78 : null,
    effective_needs_review: processed ? true : null,
    analysis_summary: processed
      ? "检测到受控的凭据访问行为，需要运营复核。"
      : null,
    analysis_reason: processed ? "规则命中且进程链符合当前检测模式。" : null,
    queue_id: processed ? "REV-MEMORY-DEV-001" : null,
    observation_id: processed ? "MPO-MEMORY-DEV-001" : null,
    aggregation_key: processed ? "AGG-MEMORY-DEV-001" : null,
    pattern_support_count: processed ? 1 : null,
    pattern_distinct_source_count: processed ? 1 : null,
    pattern_quality_gate_passed: processed ? false : null,
    pattern_consistency_ratio: processed ? 1 : null,
    memory_contexts: [],
    decision_stages: processed
      ? [
          {
            stage: "base",
            status: "applied",
            verdict: "suspicious",
            confidence: 0.78,
            needs_review: true,
            suggested_action: "needs_human_review",
            disposition: null,
            source_id: "RUN-MEMORY-DEV-001",
            summary: "Runtime base decision",
          },
        ]
      : [],
  };
}

function state(processed = false) {
  return {
    schema_version: "soc.memory_dev_workbench.v1",
    safety: {
      environment: "dev",
      database_backend: "sqlite",
      database_file: "soc-memory-dev.sqlite",
      source_data_class: "operational",
      historical_replay: true,
      internal_providers: "off_or_mock",
      tenant_policy: "disabled",
      external_action_execution: false,
    },
    source: {
      file_name: "full_alert_validation_corpus.pkl",
      sha256: "a".repeat(64),
      selected_alert_count: 14,
    },
    model: {
      mode: "llm",
      model_name: "fixture-model",
      thinking_enabled: false,
      role_verifier_enabled: false,
      role_verifier_model_name: null,
    },
    cohort: {
      tenant_id: "pingan",
      rule_code: "RPAADM_002010",
      rule_name: "GalaxyLab_T1003-SAM-Dumping",
      detection_key: "leagsoft-edr:rule_code:rpaadm_002010",
      behavior_fingerprint:
        "3ae3fec1e905d58cb356b0ba79abd83f46bdffb65f19b4416db7f388a6bb036b",
      behavior_components: [
        "command_module:updatedeploy.dll",
        "command_switch:classid",
        "command_switch:deploymenthandlerfullpath",
        "command_switch:runhandlercomserver",
        "parent_service:wuauserv",
        "process:services.exe",
        "process:svchost.exe",
        "process_image:wuaucltcore.exe",
        "process_path:windows/uus/amd64/wuaucltcore.exe",
        "target_class:windows_protected_registry_hive",
      ],
      construction_target: 5,
      held_out_target: 1,
      additional_count: 8,
    },
    progress: {
      processed_count: processed ? 1 : 0,
      construction_processed: processed ? 1 : 0,
      construction_target: 5,
      candidate_state: "collecting",
      memory_state: "not_created",
      held_out_unlocked: false,
      held_out_processed: false,
      next_alert_id: processed ? ALERT_IDS[1] : ALERT_IDS[0],
      next_action: "process_construction",
    },
    candidate: null,
    alerts: ALERT_IDS.map((_, index) =>
      alert(index, processed && index === 0),
    ).map((item, index) => ({
      ...item,
      workflow_state:
        processed && index === 1
          ? "ready"
          : processed && index > 0
            ? "locked"
            : item.workflow_state,
      can_process: processed ? index === 1 : item.can_process,
    })),
  };
}

function candidateReadyState() {
  const current = state();
  return {
    ...current,
    progress: {
      ...current.progress,
      processed_count: 5,
      construction_processed: 5,
      candidate_state: "pending_review",
      next_alert_id: null,
      next_action: "review_candidate",
    },
    candidate: {
      candidate_id: "MC-206BBCE75A96",
      status: "pending_review",
      candidate_type: "detection_lesson",
      summary: "GalaxyLab_T1003-SAM-Dumping 有风险经验候选",
      support_count: 5,
      distinct_source_count: 5,
      consistency_ratio: 1,
      source_run_id: "RUN-MEMORY-DEV-005",
      source_alert_id: "1984659",
      review_queue_id: null,
      memory_id: null,
      memory_status: null,
      retrieval_enabled: false,
      decision_directive_ready: false,
      business_lesson_ready: false,
    },
    alerts: ALERT_IDS.map((_, index) => alert(index, index < 5)).map(
      (item, index) => ({
        ...item,
        workflow_state: index < 5 ? "completed" : "locked",
        can_process: false,
        queue_id: index === 4 ? null : item.queue_id,
        pattern_support_count: index < 5 ? index + 1 : null,
        pattern_distinct_source_count: index < 5 ? index + 1 : null,
        pattern_quality_gate_passed: index === 4,
      }),
    ),
  };
}

test("runs the first DEV cohort alert through the browser workflow", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  let current = state();
  await page.route("**/api/soc/dev/memory-workbench**", async (route) => {
    if (route.request().method() === "POST") {
      current = state(true);
      await route.fulfill({
        json: {
          schema_version: "soc.memory_dev_workbench_process.v1",
          alert_id: ALERT_IDS[0],
          run_id: "RUN-MEMORY-DEV-001",
          observation_id: "MPO-MEMORY-DEV-001",
          idempotent: false,
          state: current,
        },
      });
      return;
    }
    await route.fulfill({ json: current });
  });

  await page.goto("/workspace/soc/dev/memory-validation/galaxylab");

  await expect(
    page.getByRole("heading", { name: "DEV · GalaxyLab Memory 闭环" }),
  ).toBeVisible();
  await expect(
    page.getByText("真实历史样本 · operational replay"),
  ).toBeVisible();
  await expect(page.getByText("企业专属策略 · 关闭")).toBeVisible();
  await expect(page.getByText("0/14 processed")).toBeVisible();

  await page.getByRole("button", { name: "运行", exact: true }).first().click();

  await expect(page.getByText("1/14 processed")).toBeVisible();
  await expect(page.getByText("Alert 1984426 · 模式构建")).toBeVisible();
  await expect(
    page.getByText("检测到受控的凭据访问行为，需要运营复核。"),
  ).toBeVisible();
  await expect(page.getByText("可疑 · 78%", { exact: true })).toBeVisible();
});

test("links a queue-less Pattern Candidate to standalone memory review", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  await page.route("**/api/soc/dev/memory-workbench**", async (route) => {
    await route.fulfill({ json: candidateReadyState() });
  });

  await page.goto("/workspace/soc/dev/memory-validation/galaxylab");

  await expect(page.getByText("5/14 processed")).toBeVisible();
  await expect(
    page.getByText("MC-206BBCE75A96", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /审核并决定/ })).toHaveAttribute(
    "href",
    "/workspace/soc/review/memory-candidates/MC-206BBCE75A96",
  );
});

test("shows absolute 24h Pattern counts without a threshold denominator", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  const current = candidateReadyState();
  current.alerts[5] = {
    ...current.alerts[5]!,
    workflow_state: "completed",
    pattern_support_count: 6,
    pattern_distinct_source_count: 6,
  };
  await page.route("**/api/soc/dev/memory-workbench**", async (route) => {
    await route.fulfill({ json: current });
  });

  await page.goto("/workspace/soc/dev/memory-validation/galaxylab");

  const row = page.getByRole("row").filter({ hasText: "1984919" });
  await expect(row.getByText("6 条", { exact: true })).toBeVisible();
  await expect(row.getByText("6 来源", { exact: true })).toBeVisible();
  await expect(row.getByText("6/5", { exact: true })).toHaveCount(0);
});

test("keeps behavior components and model status in non-overlapping tracks", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  const current = state();
  current.model.model_name =
    "globalai-deepseek-v4-flash-0731-long-relay-registration";
  await page.route("**/api/soc/dev/memory-workbench**", async (route) => {
    await route.fulfill({ json: current });
  });
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.goto("/workspace/soc/dev/memory-validation/galaxylab");

  const model = page.getByText(current.model.model_name, { exact: true });
  const thinking = page.getByText("Thinking", { exact: true });
  const verifier = page.getByText("Role verifier", { exact: true });
  const behaviorComponents = current.cohort.behavior_components.map(
    (component) => page.getByText(component, { exact: true }),
  );
  await expect(model).toBeVisible();
  await expect(thinking).toBeVisible();
  await expect(verifier).toBeVisible();
  for (const component of behaviorComponents) {
    await expect(component).toBeVisible();
  }

  const boxes = await Promise.all([
    model.boundingBox(),
    thinking.boundingBox(),
    verifier.boundingBox(),
    ...behaviorComponents.map((component) => component.boundingBox()),
  ]);
  expect(boxes.every((box) => box !== null)).toBe(true);
  for (let left = 0; left < boxes.length; left += 1) {
    for (let right = left + 1; right < boxes.length; right += 1) {
      expect(rectanglesOverlap(boxes[left]!, boxes[right]!)).toBe(false);
    }
  }
});

function rectanglesOverlap(
  first: { x: number; y: number; width: number; height: number },
  second: { x: number; y: number; width: number; height: number },
) {
  return !(
    first.x + first.width <= second.x ||
    second.x + second.width <= first.x ||
    first.y + first.height <= second.y ||
    second.y + second.height <= first.y
  );
}
