import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

function corpusState(processed = false) {
  const candidateAlert = {
    alert_id: "1984426",
    source_index: 0,
    observed_at: "2026-04-27T18:04:42+08:00",
    topic: "edr",
    source_type: "edr",
    source_system: "leagsoft-edr",
    product: "联软EDR",
    detection_key: "leagsoft-edr:rule_code:rpaadm_002010",
    rule_code: "RPAADM_002010",
    rule_name: "GalaxyLab_T1003-SAM-Dumping",
    category: "可疑操作行为",
    severity: "中危",
    endpoint: "10.28.20.80",
    host_name: "GALAXY-1",
    process_names: ["services.exe", "svchost.exe"],
    behavior_fingerprint: "a".repeat(64),
    behavior_components: [
      "parent_service:wuauserv",
      "process_image:wuaucltcore.exe",
    ],
    behavior_strength: "strong",
    decision_eligible: true,
    readiness: "candidate_window",
    group_id: "CG-GALAXY",
    group_alert_count: 14,
    window_alert_count: 6,
    window_start: "2026-04-27T00:00:00Z",
    window_end: "2026-04-28T00:00:00Z",
    workflow_state: processed ? "completed" : "ready",
    can_process: !processed,
    run_id: processed ? "RUN-CORPUS-1" : null,
    analysis_status: processed ? "success" : null,
    model_name: processed ? "fixture-model" : null,
    prompt_version: processed ? "soc-analysis-v34" : null,
    total_duration_ms: processed ? 1200 : null,
    output_quality: processed ? "accepted" : null,
    failure_kind: null,
    failure_message: null,
    base_verdict: processed ? "suspicious" : null,
    base_confidence: processed ? 0.72 : null,
    base_needs_review: processed ? true : null,
    effective_verdict: processed ? "false_positive" : null,
    effective_confidence: processed ? 0.95 : null,
    effective_needs_review: processed ? false : null,
    analysis_summary: processed ? "当前行为符合 Windows 更新部署模式。" : null,
    analysis_reason: processed ? "规则命中，但已确认业务行为模式一致。" : null,
    queue_id: null,
    observation_id: processed ? "MPO-CORPUS-1" : null,
    aggregation_key: processed ? "AGG-CORPUS-1" : null,
    pattern_support_count: processed ? 1 : null,
    pattern_distinct_source_count: processed ? 1 : null,
    pattern_quality_gate_passed: processed ? false : null,
    pattern_consistency_ratio: processed ? 1 : null,
    candidate_id: null,
    candidate_status: null,
    memory_id: processed ? "MEM-GALAXY" : null,
    memory_status: processed ? "confirmed" : null,
    memory_contexts: processed
      ? [
          {
            context_ref: "M-001",
            label: "Windows 更新部署正常行为",
            source_id: "MEM-GALAXY",
            summary: "相同规则和强行为指纹下可复用已审核误报结论。",
          },
        ]
      : [],
    memory_directive_applied: processed,
    memory_effect: processed ? "overridden" : null,
    decision_stages: processed
      ? [
          {
            stage: "base",
            status: "applied",
            verdict: "suspicious",
            confidence: 0.72,
            needs_review: true,
            suggested_action: "needs_human_review",
            disposition: null,
            source_id: "RUN-CORPUS-1",
            summary: "Runtime base decision",
          },
          {
            stage: "memory",
            status: "applied",
            verdict: "false_positive",
            confidence: 0.95,
            needs_review: false,
            suggested_action: "ignore",
            disposition: "ignore",
            source_id: "MEM-GALAXY",
            summary: "Confirmed Memory directive applied",
          },
        ]
      : [],
  };
  const weakAlert = {
    ...candidateAlert,
    alert_id: "1965449",
    source_index: 1,
    rule_code: "RPAADM_WEAK",
    rule_name: "Weak single alert",
    detection_key: "ptp-nids:rule_code:rpaadm_weak",
    source_type: "nids",
    topic: "nids",
    readiness: "fingerprint_missing",
    decision_eligible: false,
    group_id: "CG-WEAK",
    group_alert_count: 1,
    window_alert_count: 1,
    behavior_fingerprint: null,
    behavior_components: [],
    behavior_strength: null,
    workflow_state: "ready",
    can_process: true,
    run_id: null,
    analysis_status: null,
    model_name: null,
    prompt_version: null,
    total_duration_ms: null,
    output_quality: null,
    base_verdict: null,
    base_confidence: null,
    base_needs_review: null,
    effective_verdict: null,
    effective_confidence: null,
    effective_needs_review: null,
    analysis_summary: null,
    analysis_reason: null,
    observation_id: null,
    aggregation_key: null,
    pattern_support_count: null,
    pattern_distinct_source_count: null,
    pattern_quality_gate_passed: null,
    pattern_consistency_ratio: null,
    memory_id: null,
    memory_status: null,
    memory_contexts: [],
    memory_directive_applied: false,
    memory_effect: null,
    decision_stages: [],
  };
  return {
    schema_version: "soc.corpus_dev_workbench.v1",
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
      file_name: "full_alert_2026_month_forth_sample_200.pkl",
      sha256: "b".repeat(64),
      alert_count: 210,
    },
    model: {
      mode: "llm",
      model_name: "fixture-model",
      thinking_enabled: false,
      role_verifier_enabled: false,
      role_verifier_model_name: null,
    },
    readiness: {
      total_alert_count: 210,
      fingerprint_coverage_count: 189,
      decision_eligible_alert_count: 111,
      recurrent_group_count: 21,
      recurrent_alert_count: 124,
      candidate_window_group_count: 2,
      candidate_window_alert_count: 12,
      processed_count: processed ? 1 : 0,
      failed_count: 0,
      memory_hit_alert_count: processed ? 1 : 0,
    },
    groups: [
      {
        group_id: "CG-GALAXY",
        source_type: "edr",
        detection_key: "leagsoft-edr:rule_code:rpaadm_002010",
        rule_code: "RPAADM_002010",
        rule_name: "GalaxyLab_T1003-SAM-Dumping",
        behavior_fingerprint: "a".repeat(64),
        behavior_components: candidateAlert.behavior_components,
        decision_eligible: true,
        alert_count: 14,
        window_count: 9,
        max_window_alert_count: 6,
        candidate_window_count: 1,
        processed_count: processed ? 1 : 0,
        memory_hit_count: processed ? 1 : 0,
      },
    ],
    alerts: [candidateAlert, weakAlert],
  };
}

test("filters the corpus by Memory readiness and runs one alert", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  let current = corpusState();
  await page.route("**/api/soc/dev/corpus-workbench**", async (route) => {
    if (route.request().method() === "POST") {
      current = corpusState(true);
      await route.fulfill({
        json: {
          schema_version: "soc.corpus_dev_workbench_process.v1",
          alert_id: "1984426",
          run_id: "RUN-CORPUS-1",
          observation_id: "MPO-CORPUS-1",
          idempotent: false,
          state: current,
        },
      });
      return;
    }
    await route.fulfill({ json: current });
  });

  await page.goto("/workspace/soc/corpus-validation");

  await expect(
    page.getByRole("heading", { name: "SOC 语料验证" }),
  ).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "SOC 运营导航" });
  await expect(
    navigation.getByRole("link", { name: "运营总览" }),
  ).toHaveAttribute("href", "/workspace/soc/operations");
  await expect(
    navigation.getByRole("link", { name: "审核中心" }),
  ).toHaveAttribute("href", "/workspace/soc/review/alerts");
  await expect(
    navigation.getByRole("link", { name: "归一化运维" }),
  ).toHaveAttribute("href", "/workspace/soc/normalization");
  const currentNavigationLink = navigation.getByRole("link", {
    name: /语料验证/,
  });
  await expect(currentNavigationLink).toHaveAttribute("aria-current", "page");
  await expect(
    navigation.getByRole("link", { name: "Memory Center" }),
  ).toHaveAttribute("href", "/workspace/soc/memory");
  await expect(page.getByRole("link", { name: "GalaxyLab 闭环" })).toHaveCount(
    0,
  );
  await expect(page.getByText("210 条", { exact: true })).toBeVisible();
  await expect(
    page.getByText("GalaxyLab_T1003-SAM-Dumping").first(),
  ).toBeVisible();
  await expect(page.getByText("Weak single alert")).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(currentNavigationLink).toBeInViewport({ ratio: 1 });
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.getByRole("button", { name: "运行", exact: true }).click();

  await expect(page.getByText("Memory 已应用", { exact: true })).toBeVisible();
  await expect(page.getByText("Windows 更新部署正常行为")).toBeVisible();
  await expect(page.getByText("可疑", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("误报", { exact: true }).first()).toBeVisible();
});
