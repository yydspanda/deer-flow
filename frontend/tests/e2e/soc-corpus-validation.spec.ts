import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

function corpusState(processed = false) {
  const candidateAlert = {
    alert_id: "1984426",
    source_index: 0,
    sequence_number: 1,
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
    can_process: true,
    blocked_by_alert_id: null,
    run_id: processed ? "RUN-CORPUS-1" : null,
    analysis_status: processed ? "success" : null,
    model_name: processed ? "fixture-model" : null,
    prompt_version: processed ? "soc-analysis-v35" : null,
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
    candidate_id: null as string | null,
    candidate_status: null as string | null,
    manual_candidate_id: null,
    manual_candidate_status: null,
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
    operational_label_available: true,
    operational_label_revealed: processed,
    operational_label: processed ? "忽略" : null,
    operational_label_observed_at: processed
      ? "2026-04-28T09:00:00+08:00"
      : null,
    operational_label_method: processed ? "exported_triage_result" : null,
    operational_label_reason: processed ? "已确认更新部署行为" : null,
    operational_label_status: processed ? "已忽略" : null,
    label_temporal_status: "valid",
    base_operational_projection: processed ? "transfer" : "undetermined",
    effective_operational_projection: processed ? "ignore" : "undetermined",
    base_label_comparison: processed ? "mismatched" : "not_run",
    effective_label_comparison: processed ? "matched" : "not_run",
    base_projection_basis: processed ? "verdict:suspicious" : null,
    effective_projection_basis: processed ? "verdict:false_positive" : null,
  };
  const weakAlert = {
    ...candidateAlert,
    alert_id: "1965449",
    source_index: 1,
    sequence_number: 2,
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
    operational_label_available: false,
    operational_label_revealed: false,
    operational_label: null,
    operational_label_observed_at: null,
    operational_label_method: null,
    operational_label_reason: null,
    operational_label_status: null,
    label_temporal_status: "unlabeled",
    base_operational_projection: "undetermined",
    effective_operational_projection: "undetermined",
    base_label_comparison: "unlabeled",
    effective_label_comparison: "unlabeled",
    base_projection_basis: null,
    effective_projection_basis: null,
  };
  const contextOnlyAlert = {
    ...weakAlert,
    alert_id: "2480991",
    source_index: 2,
    sequence_number: 3,
    rule_code: "RPAADM_002010",
    rule_name: "GalaxyLab_T1003-SAM-Dumping",
    detection_key: "leagsoft-edr:rule_code:rpaadm_002010",
    source_type: "ndr",
    topic: "ndr",
    readiness: "recurrent_strong",
    decision_eligible: true,
    group_id: "CG-CONTEXT",
    group_alert_count: 2,
    window_alert_count: 2,
    behavior_fingerprint: "e".repeat(64),
    behavior_components: ["network_service:sip/5060", "protocol:udp"],
    behavior_strength: "strong",
  };
  return {
    schema_version: "soc.corpus_dev_workbench.v3",
    safety: {
      environment: "dev",
      database_backend: "sqlite",
      database_file: "soc-memory-dev.sqlite",
      source_data_class: "operational",
      historical_replay: true,
      internal_providers: "off_or_mock",
      tenant_policy: "deterministic_and_llm",
      software_path_fast_policy: true,
      external_action_execution: false,
      memory_scope: "dev-corpus-eval",
      pattern_window_days: 30,
      execution_mode: "interactive_exploration",
      chronology_enforced: false,
      rerun_enabled: true,
      causal_evaluation_allowed: false,
      replay_order: "operator_selected",
      label_visibility: "hidden_until_runtime_decision",
    },
    source: {
      file_name: "full_alert_dams_labeled_merged.pkl",
      sha256: "b".repeat(64),
      alert_count: 4343,
      labeled_alert_count: 3566,
      unlabeled_alert_count: 777,
      first_event_time: "2026-04-01T00:00:00+08:00",
      last_event_time: "2026-08-18T23:59:59+08:00",
      sort_order: "canonical_event_time_asc_alert_id_asc",
      index_file_name: "full_alert_dams_labeled_merged.workbench-index.json",
      index_sha256: "c".repeat(64),
      payload_store_file_name:
        "full_alert_dams_labeled_merged.workbench-payloads.sqlite",
      payload_store_sha256: "d".repeat(64),
    },
    model: {
      mode: "llm",
      model_name: "fixture-model",
      thinking_enabled: false,
      role_verifier_enabled: false,
      role_verifier_model_name: null,
    },
    readiness: {
      total_alert_count: 4343,
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
    evaluation: {
      label_kind: "operational_disposition",
      label_counts: { 忽略: 2798, 转交: 768 },
      temporally_valid_label_count: 3566,
      temporally_invalid_label_count: 0,
      unlabeled_count: 777,
      processed_labeled_count: processed ? 1 : 0,
      base_matched_count: 0,
      base_mismatched_count: processed ? 1 : 0,
      base_unscored_count: 0,
      base_match_rate: processed ? 0 : null,
      effective_matched_count: processed ? 1 : 0,
      effective_mismatched_count: 0,
      effective_unscored_count: 0,
      effective_match_rate: processed ? 1 : null,
    },
    leadership_demo: {
      schema_version: "soc.leadership_demo_guide.v2",
      guide_version: "fixture-demo.v2",
      title: "历史经验如何参与研判",
      purpose:
        "用同一条检测规则下的不同实际行为，对比历史经验何时只作参考、何时可以复用审核结论。",
      ready: true,
      primary_chapter_count: 2,
      backup_chapter_count: 0,
      chapters: [
        {
          chapter_id: "same-rule-context-only",
          sequence: 1,
          tier: "primary",
          expected_memory_use: "context_only",
          title: "同一规则、不同场景：经验只作研判参考",
          objective: "规则相同但行为指纹不同。",
          presenter_note: "不会仅按 rule_code 套用结论。",
          capabilities: ["同规则多场景", "Context-only"],
          operator_steps: ["查看第一组告警。", "运行告警。"],
          success_cues: ["历史经验不直接改判。"],
          targets: [
            {
              target_id: "context",
              label: "SIP/5060 · 仅作参考",
              source_type: "ndr",
              expected_group_id: "CG-CONTEXT",
              actual_group_id: "CG-CONTEXT",
              primary_alert_id: "2480991",
              rehearsal_alert_ids: ["2480991"],
              availability: "ready",
              missing_alert_ids: [],
              drifted_alert_ids: [],
            },
          ],
        },
        {
          chapter_id: "same-rule-exact-match",
          sequence: 2,
          tier: "primary",
          expected_memory_use: "exact_match",
          title: "同一规则、同一场景：复用审核结论",
          objective: "规则和行为指纹都一致。",
          presenter_note: "展示审核结论复用。",
          capabilities: ["强行为指纹", "精确匹配"],
          operator_steps: ["查看第二组告警。", "运行告警。"],
          success_cues: ["最终结论保留完整来源。"],
          targets: [
            {
              target_id: "exact",
              label: "Windows 更新进程链 · 精确复用",
              source_type: "edr",
              expected_group_id: "CG-GALAXY",
              actual_group_id: "CG-GALAXY",
              primary_alert_id: "1984426",
              rehearsal_alert_ids: ["1984426"],
              availability: "ready",
              missing_alert_ids: [],
              drifted_alert_ids: [],
            },
          ],
        },
      ],
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
      {
        group_id: "CG-CONTEXT",
        source_type: "ndr",
        detection_key: "leagsoft-edr:rule_code:rpaadm_002010",
        rule_code: "RPAADM_002010",
        rule_name: "GalaxyLab_T1003-SAM-Dumping",
        behavior_fingerprint: "e".repeat(64),
        behavior_components: contextOnlyAlert.behavior_components,
        decision_eligible: true,
        alert_count: 2,
        window_count: 1,
        max_window_alert_count: 2,
        candidate_window_count: 0,
        processed_count: 0,
        memory_hit_count: 0,
      },
    ],
    alerts: [candidateAlert, weakAlert, contextOnlyAlert],
  };
}

function activeMemoryRecord() {
  return {
    schema_version: "soc.memory_record.v1",
    memory_id: "MEM-GALAXY",
    version: 2,
    memory_type: "benign_pattern",
    target_artifact: "tenant_memory",
    status: "confirmed",
    tenant_scope: "pingan",
    tenant_id: "pingan",
    source_candidate_id: "MC-GALAXY",
    source: {
      source_type: "repeated_pattern",
      source_id: "pattern:galaxy",
      run_id: "RUN-CONSTRUCTION",
      alert_id: "1984400",
      metadata: {},
    },
    summary: "Windows 更新部署正常行为",
    content: "相同规则和强行为指纹下可复用已审核误报结论。",
    business_lesson: {
      schema_version: "soc.memory_business_lesson.v1",
      conclusion: "该行为属于已确认的 Windows 更新部署活动。",
      business_rationale: ["运营人员已核对更新任务。"],
      applicability_conditions: ["必须命中相同规则和强行为指纹。"],
      generalization_boundaries: ["不同进程链不能复用。"],
      invalidation_conditions: ["出现新的攻击证据时失效。"],
      handling_guidance: ["精确匹配时复用误报结论。"],
    },
    facets: {
      detection_key: ["leagsoft-edr:rule_code:rpaadm_002010"],
      behavior_fingerprint: ["behavior:galaxy:v1"],
    },
    applicability: null,
    evidence_refs: ["memory_pattern:galaxy"],
    validity: {
      valid_from: "2026-08-01T00:00:00Z",
      valid_until: "2026-10-30T00:00:00Z",
      review_after_days: 30,
      notes: "Reviewed fixture.",
    },
    confidence: 0.95,
    decision_impact: "detection_decision",
    decision_directive: null,
    content_hash: `sha256:${"a".repeat(64)}`,
    facets_hash: `sha256:${"b".repeat(64)}`,
    retrieval_enabled: true,
    retrieval_policy_version: "soc.memory_retrieval_activation_policy.v1",
    retrieval_valid_until: "2026-10-01T00:00:00Z",
    retrieval_review_due_at: "2026-09-01T00:00:00Z",
    retrieval_updated_by: null,
    retrieval_updated_at: "2026-08-01T00:00:00Z",
    retrieval_reason: "Reviewed fixture activation.",
    created_by: {
      actor_id: "fixture-reviewer",
      actor_type: "user",
      surface: "web",
      roles: ["soc_memory_reviewer"],
      auth_source: "session",
    },
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    labels: ["confirmed-memory", "retrieval-enabled"],
    metadata: {},
  };
}

function activeDirectiveMemoryRecord() {
  return {
    ...activeMemoryRecord(),
    decision_directive: {
      schema_version: "soc.memory_decision_directive.v1",
      effect: "override",
      target_verdict: "false_positive",
      review_effect: "clear",
      suggested_action: "精确匹配时复用已审核误报结论。",
      minimum_match_score: 5,
      required_facet_keys: [
        "behavior_fingerprint",
        "detection_key",
        "environment",
      ],
      rationale: "运营审核后的强匹配经验允许改变最终结论。",
      policy_version: "soc.memory_decision_directive_policy.v1",
    },
  };
}

function corpusExecution(processed = false) {
  const phase = (
    key: string,
    label: string,
    status: "pending" | "running" | "success",
  ) => ({
    phase: key,
    label,
    status,
    summary:
      status === "success"
        ? `${label} 已完成`
        : status === "running"
          ? `${label} 正在执行`
          : "等待上游阶段完成",
    duration_ms: status === "success" ? 100 : null,
    metrics: key === "reasoning" && processed ? { total_tokens: 1280 } : {},
    steps:
      status === "success"
        ? [
            {
              step_name: key,
              label,
              status: "success",
              duration_ms: 100,
              warning_count: 0,
            },
          ]
        : [],
  });
  return {
    schema_version: "soc.corpus_dev_execution.v1",
    alert_id: "1984426",
    status: processed ? "completed" : "not_started",
    current_phase: null,
    run_id: processed ? "RUN-CORPUS-1" : null,
    run_status: processed ? "success" : null,
    elapsed_ms: processed ? 1200 : null,
    total_duration_ms: processed ? 1200 : null,
    model_name: processed ? "fixture-model" : null,
    provider_attempt_count: processed ? 1 : 0,
    observation_id: processed ? "MPO-CORPUS-1" : null,
    phases: [
      phase(
        "normalize",
        "归一化 / Normalize",
        processed ? "success" : "pending",
      ),
      phase(
        "facts",
        "实体与事实 / Entities & Facts",
        processed ? "success" : "pending",
      ),
      phase(
        "context",
        "上下文与 Skills / Context & Skills",
        processed ? "success" : "pending",
      ),
      phase(
        "reasoning",
        "模型研判 / LLM Analysis",
        processed ? "success" : "pending",
      ),
      phase(
        "validation",
        "结果校验 / Validate",
        processed ? "success" : "pending",
      ),
      phase(
        "decision",
        "决策生成 / Decision",
        processed ? "success" : "pending",
      ),
      phase(
        "memory",
        "模式与记忆 / Pattern & Memory",
        processed ? "success" : "pending",
      ),
    ],
  };
}

function corpusAudit() {
  const artifact = (
    sequence: number,
    artifactId: string,
    fileName: string,
    title: string,
    payload: Record<string, unknown>,
  ) => ({
    sequence,
    artifact_id: artifactId,
    file_name: fileName,
    phase: artifactId,
    title,
    description: `${title} 的持久化审计产物`,
    status: "available",
    source: "persisted_run",
    metrics: { fields: Object.keys(payload).length },
    review_guide: ["核对该阶段字段和来源路径。"],
    payload,
  });
  return {
    schema_version: "soc.corpus_dev_audit_bundle.v1",
    alert_id: "1984426",
    run_id: "RUN-CORPUS-1",
    generated_at: "2026-08-20T08:00:00Z",
    pipeline_version: "soc-runtime-v8",
    model_name: "fixture-model",
    prompt_version: "soc-analysis-v4",
    input_hash: "b".repeat(64),
    safety: {
      dev_only: true,
      admin_only: true,
      contains_raw_alert_data: true,
      contains_model_context: true,
      reexecutes_runtime: false,
      mutates_state: false,
    },
    execution: corpusExecution(true),
    artifacts: [
      artifact(
        1,
        "run-manifest",
        "01-run-manifest.json",
        "运行清单 / Run Manifest",
        { run_id: "RUN-CORPUS-1", status: "success" },
      ),
      artifact(
        2,
        "source-input",
        "02-source-input.json",
        "原始输入 / Source Input",
        {
          input_payload: {
            alert: { hitLog: [{ zeusRawLogs: [{ message: "raw-message" }] }] },
          },
        },
      ),
      artifact(
        6,
        "bounded-analysis-input",
        "06-bounded-analysis-input.json",
        "模型输入与 Skills / Bounded Analysis Input",
        {
          projection_lineage: {
            status: "exact_for_prompt_version",
            run_prompt_version: "soc-analysis-v35",
            builder_prompt_version: "soc-analysis-v35",
            exact: true,
          },
          model_visible_context: {
            alert_id: "1984426",
            evidence: {
              coverage: {
                analysis_readiness: {
                  status: "ready",
                  summary: "当前主要证据已进入模型上下文。",
                },
              },
            },
          },
          runtime_request_audit: {
            alert_id: "1984426",
            primary_evidence: {
              source_path: "alert.hitLog[0].zeusRawLogs[0].message",
              projected_field_paths: ["message#parsed.source_ip"],
            },
          },
        },
      ),
    ],
  };
}

function corpusStateWithPatternCandidate() {
  const state = corpusState(true);
  const candidateAlert = state.alerts[0]!;
  return {
    ...state,
    readiness: {
      ...state.readiness,
      memory_hit_alert_count: 0,
    },
    alerts: [
      {
        ...candidateAlert,
        candidate_id: "MC-PATTERN-1",
        candidate_status: "pending_review",
        memory_id: null,
        memory_status: null,
        memory_contexts: [],
        memory_directive_applied: false,
        memory_effect: null,
      },
      state.alerts[1]!,
    ],
  };
}

test("filters the corpus by Memory readiness and runs one alert", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  let current = corpusState();
  let processCalls = 0;
  let promotionRequestBody: unknown;
  await page.route("**/api/soc/dev/corpus-workbench**", async (route) => {
    if (route.request().url().endsWith("/audit")) {
      await route.fulfill({ json: corpusAudit() });
      return;
    }
    if (route.request().url().endsWith("/execution")) {
      await route.fulfill({
        json: corpusExecution(current.readiness.processed_count > 0),
      });
      return;
    }
    if (route.request().method() === "POST") {
      processCalls += 1;
      current = corpusState(true);
      await route.fulfill({
        json: {
          schema_version: "soc.corpus_dev_workbench_process.v3",
          alert_id: "1984426",
          run_id: `RUN-CORPUS-${processCalls}`,
          observation_id: "MPO-CORPUS-1",
          idempotent: processCalls > 1,
          execution_mode: processCalls > 1 ? "rerun" : "initial",
          replay_of_run_id: processCalls > 1 ? "RUN-CORPUS-1" : null,
          pattern_observation_reused: processCalls > 1,
          state: current,
        },
      });
      return;
    }
    await route.fulfill({ json: current });
  });
  await page.route("**/api/soc/memory/runs/*/promote", async (route) => {
    promotionRequestBody = route.request().postDataJSON();
    await route.fulfill({
      json: {
        schema_version: "soc.memory_run_promotion_result.v1",
        run_id: "RUN-CORPUS-1",
        alert_id: "1984426",
        memory_candidate: { candidate_id: "MC-MANUAL-1" },
        memory_admission: {
          status: "admitted",
          reason_codes: ["explicit_promotion_requested"],
        },
      },
    });
  });

  await page.goto("/workspace/soc/corpus-validation");

  await expect(
    page.getByRole("heading", { name: "SOC 告警研判演练" }),
  ).toBeVisible();
  await expect(
    page.getByText("企业专属策略 · 全开（确定性 + 安全路径 + LLM）"),
  ).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "SOC 运营导航" });
  await expect(
    navigation.getByRole("link", { name: "运营总览" }),
  ).toHaveAttribute("href", "/workspace/soc/operations");
  await expect(
    navigation.getByRole("link", { name: "研判待办" }),
  ).toHaveAttribute("href", "/workspace/soc/review/alerts");
  await expect(
    navigation.getByRole("link", { name: "归一化运维" }),
  ).toHaveAttribute("href", "/workspace/soc/normalization");
  const currentNavigationLink = navigation.getByRole("link", {
    name: /告警演练/,
  });
  await expect(currentNavigationLink).toHaveAttribute("aria-current", "page");
  await expect(
    navigation.getByRole("link", { name: "Memory Center" }),
  ).toHaveAttribute("href", "/workspace/soc/memory");
  await expect(page.getByRole("link", { name: "GalaxyLab 闭环" })).toHaveCount(
    0,
  );
  await expect(page.getByText("4343 条", { exact: true })).toBeVisible();
  await expect(
    page.getByText("GalaxyLab_T1003-SAM-Dumping").first(),
  ).toBeVisible();
  await expect(page.getByText("Weak single alert")).toBeVisible();
  await expect(page.getByText("Runtime 运行后揭示")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "历史经验如何参与研判" }),
  ).toBeVisible();
  await expect(page.getByText("两组告警已就绪", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看这组告警" })).toHaveCount(
    2,
  );
  const exactMatchRehearsal = page.getByRole("article").filter({
    hasText: "精确匹配复用",
  });
  await exactMatchRehearsal
    .getByRole("button", { name: "查看这组告警" })
    .click();
  await expect(
    exactMatchRehearsal.getByRole("button", { name: "已显示这组" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Alert 1984426" }),
  ).toBeVisible();
  await page.getByLabel("行为模式组").click();
  await expect(
    page.getByRole("option", {
      name: /GalaxyLab_T1003-SAM-Dumping.*组 GALAXY.*14 条/,
    }),
  ).toBeVisible();
  await page.keyboard.press("Escape");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(currentNavigationLink).toBeInViewport({ ratio: 1 });
  await page.setViewportSize({ width: 1280, height: 720 });

  const corpusSearch = page.getByPlaceholder("告警编号 / 规则 / 主机 / IP");
  await corpusSearch.fill("1984426");
  await page.reload();
  await expect(corpusSearch).toHaveValue("1984426");
  await page.getByRole("button", { name: "运行", exact: true }).click();

  await expect(page.getByLabel("当前安全结论")).toContainText(
    "误报 / False Positive",
  );
  await expect(
    page.getByText("已复用审核结论", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("Windows 更新部署正常行为")).toBeVisible();
  await expect(page.getByText("可疑", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("误报", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("历史处置依据：")).toBeVisible();
  await expect(page.getByText("已确认更新部署行为")).toBeVisible();
  await expect(page.getByText("一致", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "运行轨迹 / Runtime Trace" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "运行轨迹 / Runtime Trace" }),
  ).not.toBeInViewport();
  await expect(page.getByText("Alert 1984426 研判完成")).toBeVisible();
  await page
    .getByRole("button", { name: "查看 Alert 1984426 结果" })
    .last()
    .click();
  await expect(
    page.getByRole("heading", { name: "运行轨迹 / Runtime Trace" }),
  ).toBeInViewport();
  await expect(page.getByText(/总 Token:/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "重新运行 Alert 1984426" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "重新运行 Alert 1984426" }).click();
  await expect.poll(() => processCalls).toBe(2);
  await expect(page.getByText("本次已创建新的 Runtime Run")).toBeVisible();

  await page.getByRole("button", { name: "打开完整审计" }).click();
  await expect(
    page.getByRole("heading", { name: "全链路审计 / Full Audit" }),
  ).toBeVisible();
  await expect(page.getByText("01-run-manifest.json").first()).toBeVisible();
  await page.getByRole("button", { name: /原始输入 \/ Source Input/ }).click();
  const jsonSearch = page.getByLabel("搜索 JSON");
  await jsonSearch.fill("zeusRawLogs");
  await expect(page.getByText("1 / 1", { exact: true })).toBeVisible();
  await expect(page.getByText("核对该阶段字段和来源路径。")).toBeVisible();
  await expect(page.getByRole("radio", { name: "格式化" })).toBeChecked();
  await expect(page.getByLabel("切换长行换行")).toBeVisible();
  await page
    .getByRole("button", {
      name: /模型输入与 Skills \/ Bounded Analysis Input/,
    })
    .click();
  await expect(page.getByRole("tab", { name: "模型实际可见" })).toBeVisible();
  await expect(
    page.getByRole("tab", { name: "Runtime 审计契约" }),
  ).toBeVisible();
  await page.getByLabel("搜索 JSON").fill("analysis_readiness");
  await expect(page.getByText("1 / 1", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Runtime 审计契约" }).click();
  await page.getByLabel("搜索 JSON").fill("projected_field_paths");
  await expect(page.getByText("1 / 1", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  const auditRegion = page.getByRole("region", {
    name: "SOC DEV 全链路审计",
  });
  await auditRegion.scrollIntoViewIfNeeded();
  await expect(auditRegion).toBeVisible();
  await expect(
    auditRegion.getByRole("navigation", { name: "审计阶段产物" }),
  ).toBeVisible();
  await expect(auditRegion.getByLabel("搜索 JSON")).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.getByRole("button", { name: "提炼 Candidate" }).click();
  await expect(page.getByLabel("补充说明（可选）")).toBeVisible();
  await page.getByRole("button", { name: "确认提前提炼" }).click();
  await expect(
    page.getByText("已创建待审 Candidate MC-MANUAL-1"),
  ).toBeVisible();
  expect(promotionRequestBody).toEqual({});
});

test("announces a newly generated Pattern Candidate in the current alert", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  let current = corpusState();
  await page.route("**/api/soc/dev/corpus-workbench**", async (route) => {
    if (route.request().url().endsWith("/execution")) {
      await route.fulfill({
        json: corpusExecution(current.readiness.processed_count > 0),
      });
      return;
    }
    if (route.request().method() === "POST") {
      current = corpusStateWithPatternCandidate();
      await route.fulfill({
        json: {
          schema_version: "soc.corpus_dev_workbench_process.v3",
          alert_id: "1984426",
          run_id: "RUN-CORPUS-1",
          observation_id: "MPO-CORPUS-1",
          idempotent: false,
          execution_mode: "initial",
          replay_of_run_id: null,
          pattern_observation_reused: false,
          state: current,
        },
      });
      return;
    }
    await route.fulfill({ json: current });
  });

  await page.goto("/workspace/soc/corpus-validation");
  await page.getByPlaceholder("告警编号 / 规则 / 主机 / IP").fill("1984426");
  await page.getByRole("button", { name: "运行", exact: true }).click();

  await expect(
    page.getByText("同类模式候选 MC-PATTERN-1", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "立即审核" })).toHaveAttribute(
    "href",
    "/workspace/soc/review/memory-candidates/MC-PATTERN-1",
  );
  await expect(page.getByRole("link", { name: "审核并决定" })).toHaveCount(0);
  await expect(page.getByText("Memory Candidate 已生成").first()).toBeVisible();
});

test("opens a used Memory correction and creates a governed revision candidate", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  await page.route("**/api/soc/dev/corpus-workbench**", async (route) => {
    if (route.request().url().endsWith("/execution")) {
      await route.fulfill({ json: corpusExecution(true) });
      return;
    }
    await route.fulfill({ json: corpusState(true) });
  });
  let revisionRequest: Record<string, unknown> | null = null;
  await page.route("**/api/soc/memory/records/MEM-GALAXY**", async (route) => {
    if (route.request().method() === "POST") {
      revisionRequest = route.request().postDataJSON() as Record<
        string,
        unknown
      >;
      await route.fulfill({
        json: {
          schema_version: "soc.memory_revision_candidate_create_result.v1",
          candidate: { candidate_id: "MC-REVISION-1" },
          predecessor_record: {
            ...activeMemoryRecord(),
            version: 3,
            retrieval_enabled: false,
          },
          previous_record_version: 2,
          previous_retrieval_enabled: true,
          audit_id: "SMA-REVISION-1",
          created_at: "2026-08-21T08:00:00Z",
        },
      });
      return;
    }
    await route.fulfill({ json: activeDirectiveMemoryRecord() });
  });

  await page.goto("/workspace/soc/corpus-validation");
  await page.getByRole("switch", { name: "仅显示未运行告警" }).click();
  await page.getByPlaceholder("告警编号 / 规则 / 主机 / IP").fill("1984426");
  await page.getByRole("link", { name: "纠正此 Memory" }).click();
  await expect(page).toHaveURL(
    /\/workspace\/soc\/memory\/records\/MEM-GALAXY\/revise\?run_id=RUN-CORPUS-1/,
  );
  await expect(page.getByRole("heading", { name: "纠正经验" })).toBeVisible();
  await expect(page.getByText("RUN-CORPUS-1", { exact: true })).toBeVisible();
  await expect(
    page.getByText("精确匹配可复用结论", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/只有部分条件相似时，仅供模型参考/),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("radio", { name: "范围过宽" })).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth,
    ),
  ).toBe(true);

  await page.getByRole("radio", { name: "范围过宽" }).click();
  await page
    .getByLabel("2. 说明本次发现的业务事实或反证")
    .fill(
      "本次告警的进程链与旧经验不同，旧 Memory 的适用范围过宽，需要重新收窄。",
    );
  await page.getByRole("button", { name: "暂停旧经验并创建修订候选" }).click();

  await expect.poll(() => revisionRequest).not.toBeNull();
  expect(revisionRequest).toEqual({
    expected_record_version: 2,
    source_run_id: "RUN-CORPUS-1",
    issue_type: "applicability_too_broad",
    reason:
      "本次告警的进程链与旧经验不同，旧 Memory 的适用范围过宽，需要重新收窄。",
  });
  await expect(page).toHaveURL(
    "/workspace/soc/review/memory-candidates/MC-REVISION-1",
  );
});

test("creates an operator-direct revision from the Memory inventory", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  let revisionRequest: Record<string, unknown> | null = null;
  await page.route("**/api/soc/memory/records/MEM-GALAXY**", async (route) => {
    if (route.request().method() === "POST") {
      revisionRequest = route.request().postDataJSON() as Record<
        string,
        unknown
      >;
      await route.fulfill({
        json: {
          schema_version: "soc.memory_revision_candidate_create_result.v1",
          candidate: { candidate_id: "MC-DIRECT-REVISION-1" },
          predecessor_record: {
            ...activeMemoryRecord(),
            version: 3,
            retrieval_enabled: false,
          },
          previous_record_version: 2,
          previous_retrieval_enabled: true,
          audit_id: "SMA-DIRECT-REVISION-1",
          created_at: "2026-08-21T08:00:00Z",
        },
      });
      return;
    }
    await route.fulfill({ json: activeMemoryRecord() });
  });

  await page.goto("/workspace/soc/memory/records/MEM-GALAXY/revise");
  await expect(page.getByText("运营人员直接修订")).toBeVisible();
  await expect(page.getByText("仅供研判参考", { exact: true })).toBeVisible();
  await page.getByRole("radio", { name: "经验不完整" }).click();
  await page
    .getByLabel("2. 说明本次发现的业务事实或反证")
    .fill(
      "运营人员发现该经验遗漏了明确的适用边界，需要创建新版本补充后再启用。",
    );
  await page.getByRole("button", { name: "暂停旧经验并创建修订候选" }).click();

  await expect.poll(() => revisionRequest).not.toBeNull();
  expect(revisionRequest).toEqual({
    expected_record_version: 2,
    issue_type: "lesson_incomplete",
    reason:
      "运营人员发现该经验遗漏了明确的适用边界，需要创建新版本补充后再启用。",
  });
  await expect(page).toHaveURL(
    "/workspace/soc/review/memory-candidates/MC-DIRECT-REVISION-1",
  );
});

test("searches confirmed Memory records and opens their usage history", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  await page.route("**/api/soc/memory/records?**", async (route) => {
    await route.fulfill({
      json: {
        items: [activeMemoryRecord()],
        limit: 50,
        offset: 0,
        has_more: false,
      },
    });
  });
  await page.route(
    "**/api/soc/memory/records/MEM-GALAXY/lineage",
    async (route) => {
      await route.fulfill({
        json: {
          schema_version: "soc.memory_lineage_report.v1",
          record: activeMemoryRecord(),
          uses: [
            {
              schema_version: "soc.memory_use.v1",
              use_id: "MU-GALAXY-1",
              memory_id: "MEM-GALAXY",
              memory_version: 2,
              run_id: "RUN-LATER-1",
              alert_id: "1984426",
              base_verdict: "suspicious",
              effective_verdict: "false_positive",
              effect: "overridden",
              directive_applied: true,
              created_at: "2026-08-02T00:00:00Z",
            },
          ],
          feedback: [],
          health: [],
          revision_proposals: [],
        },
      });
    },
  );

  await page.goto("/workspace/soc/memory/records");
  await page.getByLabel("搜索经验台账").fill("MEM-GALAXY Windows 更新");
  await page.getByTitle("搜索").click();
  await expect(page.getByText("Windows 更新部署正常行为")).toBeVisible();
  await page.getByText("Windows 更新部署正常行为").click();
  await expect(page).toHaveURL("/workspace/soc/memory/records/MEM-GALAXY");
  await expect(
    page.getByText("该行为属于已确认的 Windows 更新部署活动。"),
  ).toBeVisible();
  await expect(page.getByText("Alert 1984426")).toBeVisible();
  await expect(page.getByText("改变最终结论", { exact: true })).toBeVisible();
});
