import type { Page, Route } from "@playwright/test";

const NOW = "2026-07-20T08:00:00Z";

export interface MockSocApiOptions {
  queueStatus?: "open" | "closed";
  includeQueueItem?: boolean;
  standaloneMemoryCandidate?: boolean;
  candidateStatus?: string;
}

export interface MockSocRequest {
  method: string;
  path: string;
  body: unknown;
  idempotencyKey: string | null;
}

export interface MockSocApiState {
  requests: MockSocRequest[];
  queueStatus: "open" | "closed";
  candidateStatus: string;
  normalizationStatus: string;
  includeQueueItem: boolean;
  standaloneMemoryCandidate: boolean;
}

function queueItem(state: MockSocApiState) {
  return {
    schema_version: "soc.review_queue_item.v1",
    queue_id: "REV-ALPHA-001",
    run_id: "RUN-ALPHA-001",
    alert_id: "ALT-ALPHA-001",
    tenant_id: "tenant-alpha",
    status: state.queueStatus,
    priority: "high",
    reason: "fact_conflict",
    source_type: "ndr",
    source_system: "alpha-fixture",
    rule_code: "APT-REVERSE-SHELL",
    rule_name: "Reverse shell activity",
    severity: "high",
    category: "command_and_control",
    verdict: "needs_review",
    confidence: 0.72,
    review_reasons: ["fact_conflict"],
    entity_keys: ["ip:203.0.113.7", "host:workstation-01"],
    summary: "Potential reverse shell with conflicting network roles.",
    created_at: NOW,
    updated_at: NOW,
    closed_at: state.queueStatus === "closed" ? NOW : null,
    closed_by: null,
    close_reason:
      state.queueStatus === "closed" ? "Alpha fixture closed" : null,
  };
}

function memoryCandidate(state: MockSocApiState) {
  return {
    schema_version: "soc.memory_candidate.v1",
    candidate_id: "MC-ALPHA-001",
    candidate_type: "detection_lesson",
    target_artifact: "tenant_memory",
    summary: "Authorized scanner pattern",
    content: "Confirm the change window before suppressing this pattern.",
    tenant_scope: "tenant",
    tenant_id: "tenant-alpha",
    status: state.candidateStatus,
    source: {
      source_type: "analyst_feedback",
      source_surface: "web",
      source_id: "feedback-alpha-001",
      run_id: "RUN-ALPHA-001",
      alert_id: "ALT-ALPHA-001",
      queue_id: state.standaloneMemoryCandidate ? null : "REV-ALPHA-001",
      metadata: {},
    },
    evidence_refs: state.standaloneMemoryCandidate
      ? ["analysis_run:RUN-ALPHA-001", "alert:ALT-ALPHA-001"]
      : ["review_queue:REV-ALPHA-001"],
    validity: {
      valid_from: NOW,
      valid_until: null,
      review_after_days: 30,
      notes: "Alpha browser fixture",
    },
    idempotency_key: "memory-candidate-alpha-001",
    confidence: 0.8,
    facets: {
      source_type: ["nids"],
      detection_key: ["pingan:ndr:reverse-shell"],
      behavior_fingerprint: ["behavior-alpha"],
      behavior_component: ["technique:t1059"],
      environment: ["prd"],
    },
    applicability: {
      schema_version: "soc.memory_applicability.v1",
      profile_id: "pingan.soc",
      profile_version: "2",
      feature_schema_version: "pingan.soc.memory_features.v2",
      required_facets: {
        detection_key: ["pingan:ndr:reverse-shell"],
        behavior_fingerprint: ["behavior-alpha"],
        environment: ["prd"],
      },
      optional_facets: {
        source_type: ["nids"],
        behavior_component: ["technique:t1059"],
      },
      excluded_facets: {},
      minimum_optional_matches: 0,
      minimum_strong_anchor_matches: 2,
      context_only_required_facet_keys: ["detection_key", "environment"],
      context_only_missing_facet_keys: ["behavior_fingerprint"],
      context_only_similarity_facet_keys: ["behavior_component"],
      policy_version: "soc.memory_applicability_policy.v1",
    },
    decision_impact: "detection_decision",
    runtime_decision_allowed: false,
    review_required: true,
    review_owner: "soc_memory_reviewer",
    reviewed_by: null,
    reviewed_at: null,
    review_reason: null,
    labels: ["candidate-only"],
    metadata: {},
    proposed_by: null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function memoryRecord(state: MockSocApiState) {
  return {
    schema_version: "soc.memory_record.v1",
    memory_id: "MEM-ALPHA-001",
    version: 1,
    memory_type: "detection_lesson",
    target_artifact: "tenant_memory",
    status: "confirmed",
    tenant_scope: "tenant",
    tenant_id: "tenant-alpha",
    source_candidate_id: "MC-ALPHA-001",
    source: memoryCandidate(state).source,
    summary: "Confirmed authorized scanner pattern",
    content: "Use only within the governed validity window.",
    business_lesson: {
      schema_version: "soc.memory_business_lesson.v1",
      conclusion: "该模式是已确认的内部服务调用，应按审核范围复用误报结论。",
      business_rationale: ["运营专家已核对当前告警证据和内部服务登记信息。"],
      applicability_conditions: [
        "Required canonical facet detection_key: pingan:ndr:reverse-shell",
        "Required canonical facet behavior_fingerprint: behavior-alpha",
        "Required canonical facet environment: prd",
      ],
      generalization_boundaries: ["审核范围未约束的源和目的 IP 可以变化。"],
      invalidation_conditions: ["必需 facet 不匹配或当前证据出现反证时失效。"],
      handling_guidance: ["全部适用条件命中时复用误报结论，否则重新研判。"],
    },
    facets: { source_type: ["ndr"] },
    evidence_refs: ["review_queue:REV-ALPHA-001"],
    validity: memoryCandidate(state).validity,
    confidence: 0.8,
    decision_impact: "bounded_context_only",
    content_hash: "a".repeat(64),
    facets_hash: "b".repeat(64),
    retrieval_enabled: false,
    retrieval_policy_version: null,
    retrieval_valid_until: null,
    retrieval_review_due_at: null,
    retrieval_updated_by: null,
    retrieval_updated_at: null,
    retrieval_reason: null,
    created_by: { actor_id: "memory-reviewer", surface: "web" },
    created_at: NOW,
    updated_at: NOW,
    labels: ["confirmed"],
    metadata: {},
  };
}

function dispositionProposal() {
  return {
    schema_version: "soc.disposition_proposal.v1",
    proposal_id: "DPROP-ALPHA-001",
    proposal_key: "proposal-alpha-001",
    run_id: "RUN-ALPHA-001",
    alert_id: "ALT-ALPHA-001",
    queue_id: "REV-ALPHA-001",
    source_enrichment_id: "AENRICH-ALPHA-001",
    source_query_hash: "c".repeat(64),
    source_matcher_policy_version: "soc.authorization_matcher.v1",
    source_fact_refs: [],
    source_evidence_refs: ["review_queue:REV-ALPHA-001"],
    detection_truth: {
      schema_version: "soc.detection_truth_snapshot.v1",
      verdict: "true_positive",
      confidence: 0.8,
      source: "decision",
    },
    proposed_disposition: "closed_benign_true_positive",
    reason_code: "authorized_activity_exact_match",
    rationale: ["Exact authorized-activity match remains shadow-only."],
    policy_version: "soc.disposition_proposal.v1",
    idempotency_key: "proposal-alpha-001",
    created_by: { actor_id: "soc-agent", surface: "api" },
    created_at: NOW,
    proposal_mode: "shadow",
    application_status: "not_applied",
    requires_human_review: true,
    auto_close_allowed: false,
    detection_truth_impact: "none",
    review_queue_impact: "none",
  };
}

function approvalRequest() {
  return {
    schema_version: "soc.agent_approval_request.v1",
    approval_request_id: "APR-ALPHA-001",
    permission_decision_id: "PERM-ALPHA-001",
    route: "response.block_ip",
    action: "response.block_ip",
    risk_level: "high_risk",
    reason: "High-risk response requires a persisted human approval.",
    requested_by: {
      actor_id: "soc-agent",
      surface: "web",
      roles: ["soc_analyst"],
    },
    source_proposal_id: "PROPOSAL-ALPHA-001",
    action_payload: { ip: "203.0.113.7" },
    context_refs: { queue_id: "REV-ALPHA-001" },
    status: "pending",
    created_at: NOW,
    resolved_at: null,
    resolved_by: null,
    resolution_reason: null,
    resolution_idempotency_key: null,
    resolution_expires_in_seconds: null,
    approval_grant_id: null,
  };
}

function investigationContext(state: MockSocApiState) {
  const item = queueItem(state);
  const investigationAddendum = {
    schema_version: "soc.investigation_addendum.v1",
    addendum_id: "IADD-ALPHA-001",
    projection_version: "soc-investigation-addendum-v1",
    source_report_id: "ISHR-ALPHA-001",
    source_hash: "0".repeat(64),
    execution_id: "ENRICH-ALPHA-001",
    run_id: item.run_id,
    alert_id: item.alert_id,
    trigger: "batch",
    execution_status: "completed",
    generated_at: NOW,
    source_updated_at: NOW,
    base_runtime_status: "needs_review",
    base_runtime_verdict: "needs_review",
    summary:
      "Read-only investigation completed: 1 hit, 0 not found, 0 unresolved or failed; 1/1 planned actions have persisted evidence.",
    items: [
      {
        plan_action_id: "EPA-ALPHA-001",
        route: "threat_intel.ip_reputation.lookup",
        action: "threat_intel.ip_reputation.lookup",
        adapter_id: "mock-ti",
        status: "success",
        attempt_count: 1,
        retry_count: 0,
        provider_invoked: true,
        result_mode: "mock",
        evidence_id: "EVI-ALPHA-001",
        evidence_available: true,
        evidence_summary:
          "Local fixture returned an explicit mock reputation result.",
        latest_attempt_latency_ms: 12,
      },
    ],
    evidence_refs: ["EVI-ALPHA-001"],
    evidence_coverage_ratio: 1,
    analyst_attention_required: false,
    measurement_gaps: ["provider_cost_not_measured"],
    addendum_kind: "read_only_execution_summary",
    reasoning_status: "not_requested",
    new_conclusion_produced: false,
    grounding_status: "deterministic_evidence_reference_check",
    projection_persisted: false,
    durable_sources_persisted: true,
    shadow_only: true,
    decision_impact: "none",
    base_run_mutated: false,
    automation_allowed: false,
    auto_close_allowed: false,
    confirmed_memory_write_allowed: false,
    high_risk_actions_allowed: false,
  };
  return {
    schema_version: "soc.investigation_context.v1",
    queue_item: item,
    run: {
      run_id: item.run_id,
      alert_id: item.alert_id,
      status: "needs_review",
      pipeline_version: "soc-runtime.v1",
      model_name: "stub",
      prompt_version: "soc-alert-analysis.v1",
      started_at: NOW,
      ended_at: NOW,
      decision: { verdict: "needs_review", confidence: 0.72 },
      corrections: [],
    },
    summary: {
      run_id: item.run_id,
      alert_id: item.alert_id,
      verdict: "needs_review",
      confidence: 0.72,
      summary: item.summary,
    },
    audit_records: [
      { audit_id: "AUD-ALPHA-001", action: "analysis", created_at: NOW },
    ],
    similar_alerts: [],
    action_evidence: [
      {
        schema_version: "soc.investigation_evidence.v1",
        evidence_id: "EVI-ALPHA-001",
        source_type: "read_only_action_result",
        route: "threat_intel.ip_reputation.lookup",
        action: "threat_intel.ip_reputation.lookup",
        status: "success",
        message: "Local fixture returned an explicit mock reputation result.",
        result_payload: { mocked: true, reputation_found: true, score: 76 },
        queue_id: item.queue_id,
        run_id: item.run_id,
        alert_id: item.alert_id,
        actor: { actor_id: "soc-agent", surface: "api" },
        created_at: NOW,
      },
    ],
    investigation_addenda: [investigationAddendum],
    authorization_enrichments: [],
    disposition_proposals: [dispositionProposal()],
    disposition_outcomes: [],
    external_dispositions: [],
    memory_candidates: [memoryCandidate(state)],
    relevant_memories: {
      schema_version: "soc.memory_retrieval_result.v2",
      policy_version: "soc.memory_retrieval_policy.v1",
      query: { require_retrieval_enabled: true },
      matches: [],
      total_candidate_count: 1,
      skipped_retrieval_disabled: 1,
      skipped_ungoverned_activation: 0,
      skipped_activation_expired: 0,
      skipped_review_overdue: 0,
      skipped_status: 0,
      skipped_expired: 0,
      skipped_missing_strong_anchor: 0,
      skipped_not_applicable: 0,
      skipped_below_min_score: 0,
      returned_count: 0,
      returned_context_only_count: 0,
      total_token_estimate: 0,
      max_tokens: 1200,
      created_at: NOW,
    },
    correlation_result: null,
    domain_triage_results: [],
    investigation_view: {
      schema_version: "soc.unified_investigation_view.v1",
      view_id: "VIEW-ALPHA-001",
      queue_id: item.queue_id,
      run_id: item.run_id,
      alert_id: item.alert_id,
      generated_at: NOW,
      runtime_verdict: "needs_review",
      runtime_confidence: 0.72,
      needs_review: true,
      automation_allowed: false,
      primary_summary: item.summary,
      primary_reason: item.reason,
      correlation_result: null,
      domain_triage_results: [],
      investigation_addenda: [investigationAddendum],
      evidence_timeline: [],
      counts: {
        action_evidence: 1,
        investigation_addenda: 1,
        correlation_matches: 0,
        domain_findings: 0,
        memory_candidates: 1,
        relevant_memories: 0,
        timeline_items: 0,
      },
      boundary_notes: ["Mock evidence never enables automation."],
      metadata: {},
    },
  };
}

function alertResult(state: MockSocApiState) {
  const item = queueItem(state);
  return {
    schema_version: "soc.alert_result.v1",
    summary: {
      run_id: item.run_id,
      alert_id: item.alert_id,
      tenant_id: item.tenant_id,
      source_type: item.source_type,
      source_system: item.source_system,
      detection_key: "pingan:ndr:reverse-shell",
      rule_code: item.rule_code,
      rule_name: item.rule_name,
      severity: item.severity,
      category: item.category,
      entity_keys: item.entity_keys,
      status: "needs_review",
      verdict: item.verdict,
      confidence: item.confidence,
      needs_review: true,
      review_reasons: item.review_reasons,
      summary: item.summary,
      recommended_action: "Investigate current network roles",
      created_at: NOW,
      updated_at: NOW,
    },
    attention_level: "required",
    attention_reasons: ["fact_conflict"],
    decision_usability: "degraded",
    requires_human_intervention: true,
    queue_item: item,
  };
}

function alertInvestigationContext(state: MockSocApiState) {
  const legacy = investigationContext(state);
  const { queue_item, summary, ...context } = legacy;
  void queue_item;
  void summary;
  return {
    ...context,
    schema_version: "soc.alert_investigation_context.v1",
    result: alertResult(state),
    run: {
      ...legacy.run,
      analysis: {
        verdict: "suspicious",
        confidence: 0.72,
        summary: "Potential reverse shell requires role verification.",
        reason: "Current evidence supports a suspicious network behavior.",
        recommended_action: "Investigate current network roles",
        evidence_gaps: ["Missing confirmed asset ownership"],
        manual_checks: ["Verify the destination host business owner"],
        scenario_assessments: [
          {
            scenario_name: "Reverse shell",
            is_primary: true,
          },
        ],
      },
      decision: {
        verdict: "suspicious",
        confidence: 0.72,
        reason: "Current evidence supports a suspicious network behavior.",
      },
    },
  };
}

function sampleManifest() {
  return {
    schema_version: "soc.disposition_sample_manifest.v1",
    sample_id: "DSAMPLE-ALPHA-001",
    sample_key: "sample-alpha-001",
    scope: {
      schema_version: "soc.disposition_evaluation_scope.v1",
      tenant_id: "tenant-alpha",
      environment: "test",
      window_start: NOW,
      window_end: "2026-07-21T08:00:00Z",
      proposal_policy_version: "soc.disposition_proposal.v1",
      matcher_policy_version: "soc.authorization_matcher.v1",
    },
    scope_hash: "d".repeat(64),
    population_count: 1,
    population_hash: "e".repeat(64),
    selected_proposal_ids: ["DPROP-ALPHA-001"],
    sample_size: 1,
    selection_seed_hash: "f".repeat(64),
    sampling_method: "sha256_rank_v1",
    idempotency_key: "sample-alpha-001",
    created_by: { actor_id: "qa-reviewer", surface: "web" },
    created_at: NOW,
    shadow_only: true,
    decision_impact: "none",
  };
}

function normalizationIssue(state: MockSocApiState) {
  return {
    schema_version: "soc.normalization_maintenance_issue.v1",
    issue_id: "NORM-ALPHA-001",
    dedupe_key: "normalization-alpha-001",
    issue_type: "novel_schema",
    severity: "warning",
    status: state.normalizationStatus,
    tenant_id: "tenant-alpha",
    source_system: "alpha-fixture",
    adapter: "pingan_platform",
    parser_name: "zeus-message",
    parser_version: "v1",
    schema_fingerprint: "schema-alpha-001",
    source_path: "zeusRawLogs[].message",
    expected_target: "entities.network.source_ip",
    run_id: "RUN-ALPHA-001",
    alert_id: "ALT-ALPHA-001",
    occurrence_count: 2,
    first_seen_at: NOW,
    last_seen_at: NOW,
    resolution_reason:
      state.normalizationStatus === "open"
        ? null
        : "Reviewed in browser regression",
    details: {},
  };
}

function operationsSnapshot() {
  return {
    schema_version: "soc.operations_snapshot.v1",
    generated_at: NOW,
    persisted: {
      availability: "available",
      backend: "sqlite",
      metrics: {
        measurement_scope: "lifetime",
        analysis_run_count: 18,
        analysis_run_status_counts: {
          failed: 2,
          needs_review: 4,
          success: 12,
        },
        latest_analysis_started_at: NOW,
        latest_analysis_completed_at: NOW,
        open_review_count: 4,
        oldest_open_review_created_at: NOW,
        pending_approval_request_count: 1,
        oldest_pending_approval_created_at: NOW,
        open_normalization_issue_count: 3,
        critical_open_normalization_issue_count: 1,
        active_normalization_baseline_count: 6,
        pending_memory_candidate_count: 2,
      },
    },
    kafka: {
      availability: "not_measured",
      enabled: true,
      settings_valid: true,
      checked: false,
      reachable: null,
      bootstrap_server_count: 1,
      alert_topic_count: 1,
      approval_request_topic_count: 1,
      dead_letter_configured: true,
      consumer_lag_availability: "not_measured",
      error_code: null,
    },
    measurement_gaps: [
      {
        metric: "kafka.consumer_lag",
        availability: "not_measured",
        reason:
          "PI-04-A does not collect consumer-group offsets or broker lag.",
      },
      {
        metric: "model.compute_utilization",
        availability: "not_measured",
        reason:
          "PI-04-A does not collect provider-side compute, GPU, or capacity telemetry.",
      },
      {
        metric: "production.slo_compliance",
        availability: "not_measured",
        reason:
          "Production SLO thresholds and time-window evidence are not yet approved.",
      },
    ],
    production_slo_evidence_available: false,
  };
}

function rateMetric(
  metricId: string,
  numerator: number,
  denominator: number,
  interpretation: string,
) {
  return {
    metric_id: metricId,
    availability: denominator > 0 ? "available" : "not_measured",
    numerator,
    denominator,
    value: denominator > 0 ? numerator / denominator : null,
    formula: `${numerator} / ${denominator}`,
    interpretation,
  };
}

function effectivenessSnapshot() {
  return {
    schema_version: "soc.effectiveness_snapshot.v1",
    generated_at: NOW,
    availability: "available",
    scope: {
      schema_version: "soc.effectiveness_scope.v1",
      window_start: "2026-07-18T08:00:00Z",
      window_end: NOW,
      tenant_id: null,
      source_type: null,
    },
    coverage: {
      total_alert_count: 120,
      completed_alert_count: 120,
      superseded_run_count: 3,
      labeled_alert_count: 10,
      high_trust_labeled_alert_count: 10,
      label_coverage: rateMetric(
        "quality.label_coverage",
        10,
        120,
        "只有形成最终结论的告警进入质量分母。",
      ),
      high_trust_label_coverage: rateMetric(
        "quality.high_trust_label_coverage",
        10,
        120,
        "具名人工或可信外部系统确认。",
      ),
    },
    summary: {
      triage_accuracy: rateMetric(
        "quality.triage_accuracy",
        10,
        10,
        "Effective Verdict 与最终技术结论一致。",
      ),
      detection_miss_rate: rateMetric(
        "quality.detection_miss_rate",
        0,
        1,
        "真实攻击被技术研判为误报。",
      ),
      operational_miss_rate: rateMetric(
        "quality.operational_miss_rate",
        0,
        1,
        "真实攻击被实际自动忽略。",
      ),
      transfer_precision: rateMetric(
        "quality.transfer_precision",
        1,
        1,
        "有标签的转交中最终为真实攻击。",
      ),
      attack_transfer_recall: rateMetric(
        "quality.attack_transfer_recall",
        1,
        1,
        "最终真实攻击中被转交的比例。",
      ),
      auto_ignore_rate: rateMetric(
        "automation.auto_ignore_rate",
        90,
        120,
        "已实际应用忽略类处置。",
      ),
      wrong_auto_ignore_rate: rateMetric(
        "automation.wrong_auto_ignore_rate",
        0,
        9,
        "自动忽略后最终为真实攻击。",
      ),
      human_touch_rate: rateMetric(
        "operations.human_touch_rate",
        10,
        120,
        "发生人工最终确认。",
      ),
    },
    compute: {
      run_count: 120,
      provider_run_count: 120,
      provider_call_count: 120,
      token_measured_run_count: 120,
      input_tokens: 420000,
      output_tokens: 60000,
      total_tokens: 480000,
      average_tokens_per_measured_run: 4000,
      duration_measured_run_count: 120,
      average_total_duration_ms: 1250,
      repair_run_count: 2,
      fallback_run_count: 0,
      degraded_run_count: 1,
      token_measurement_coverage: rateMetric(
        "compute.token_measurement_coverage",
        120,
        120,
        "Provider 返回可审计 usage。",
      ),
      repair_rate: rateMetric(
        "compute.repair_rate",
        2,
        120,
        "模型输出经过机械修复。",
      ),
      fallback_rate: rateMetric(
        "compute.fallback_rate",
        0,
        120,
        "退回确定性分析。",
      ),
      degraded_rate: rateMetric(
        "compute.degraded_rate",
        1,
        120,
        "存在局部降级。",
      ),
    },
    rules: [
      {
        schema_version: "soc.rule_effectiveness.v1",
        group_key: "0123456789abcdef",
        tenant_id: "tenant-alpha",
        source_type: "nids",
        source_system: "alpha-fixture",
        detection_identity: "alpha:nids:RC-ALPHA-001",
        detection_key: "alpha:nids:RC-ALPHA-001",
        rule_code: "RC-ALPHA-001",
        rule_name: "重复外联检测",
        alert_count: 120,
        completed_count: 120,
        labeled_count: 10,
        high_trust_labeled_count: 10,
        label_coverage: 10 / 120,
        final_risk_count: 1,
        final_false_positive_count: 9,
        confirmed_risk_rate: 0.1,
        false_positive_rate: 0.9,
        triage_accuracy: 1,
        miss_rate: 0,
        transfer_precision: 1,
        auto_ignore_rate: 0.75,
        wrong_auto_ignore_count: 0,
        provider_call_count: 120,
        provider_run_count: 120,
        total_tokens: 480000,
        average_total_duration_ms: 1250,
        repair_run_count: 2,
        fallback_run_count: 0,
        degraded_run_count: 1,
        memory_context_use_count: 48,
        memory_directive_use_count: 36,
        memory_contradiction_count: 0,
        recommendation: {
          schema_version: "soc.rule_improvement_recommendation.v1",
          kind: "fast_path_candidate",
          priority: "medium",
          title: "评估受治理快速路径",
          rationale: ["高量且已标注误报模式稳定。"],
          suggested_next_step:
            "先收紧到精确行为指纹和已审核 Memory/Policy，再灰度验证并保留抽样复核。",
          reason_codes: [
            "high_volume",
            "stable_false_positive_outcome",
            "model_compute_present",
          ],
          policy_version: "soc.rule_optimization_policy.v1",
          authority: "advisory",
          status: "candidate",
        },
      },
    ],
    recommendation_policy_version: "soc.rule_optimization_policy.v1",
    aggregation_mode: "latest_run_per_alert_sql_v1",
    error_code: null,
    measurement_notes: ["Fixture values verify presentation only."],
  };
}

function ruleEffectivenessDetail() {
  const snapshot = effectivenessSnapshot();
  return {
    schema_version: "soc.rule_effectiveness_detail.v1",
    generated_at: NOW,
    scope: snapshot.scope,
    rule: snapshot.rules[0],
    behavior_groups: [
      {
        schema_version: "soc.behavior_group_effectiveness.v1",
        lineage_key: "a".repeat(64),
        behavior_label: "OpenVPN / UDP 1194",
        environment: "dev",
        data_class: "simulation",
        sample_count: 8,
        distinct_alert_count: 8,
        window_count: 2,
        verdict_counts: { false_positive: 8 },
        first_observed_at: "2026-08-20T08:00:00Z",
        last_observed_at: NOW,
        candidate_id: "MC-ALPHA-001",
        candidate_status: "confirmed",
        memory_id: "MEM-ALPHA-001",
        memory_version: 2,
        memory_status: "confirmed",
        retrieval_enabled: true,
      },
      {
        schema_version: "soc.behavior_group_effectiveness.v1",
        lineage_key: "b".repeat(64),
        behavior_label: "CVE-2017-7924 / UDP 44818",
        environment: "dev",
        data_class: "simulation",
        sample_count: 3,
        distinct_alert_count: 3,
        window_count: 1,
        verdict_counts: { true_positive: 2, suspicious: 1 },
        first_observed_at: "2026-08-27T08:00:00Z",
        last_observed_at: NOW,
        candidate_id: null,
        candidate_status: null,
        memory_id: null,
        memory_version: null,
        memory_status: null,
        retrieval_enabled: false,
      },
    ],
    memories: [
      {
        schema_version: "soc.memory_effectiveness.v1",
        memory_id: "MEM-ALPHA-001",
        memory_version: 2,
        summary: "内部 OpenVPN 服务访问的稳定误报经验",
        record_status: "confirmed",
        retrieval_enabled: true,
        use_alert_count: 8,
        context_only_count: 2,
        directive_count: 6,
        high_trust_feedback_count: 5,
        support_count: 5,
        contradiction_count: 0,
        not_applicable_count: 0,
        helpful_correction_count: 4,
        harmful_override_count: 0,
        wrong_auto_ignore_count: 0,
        final_outcome_coverage: rateMetric(
          "memory.final_outcome_coverage",
          5,
          8,
          "已有运营最终反馈的使用告警占比。",
        ),
        directive_accuracy: rateMetric(
          "memory.directive_accuracy",
          5,
          5,
          "直接复用结论与高可信最终反馈一致。",
        ),
        source_rule_codes: ["RC-ALPHA-001"],
        actual_rule_codes: ["RC-ALPHA-001"],
        last_use_at: NOW,
        last_feedback_at: NOW,
        causal_note:
          "directive_effects_attributable_context_effects_non_causal",
      },
    ],
    relationship_note: "memory_rule_relationship_derived_from_actual_runs",
  };
}

async function fulfill(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType:
      status >= 400 ? "application/problem+json" : "application/json",
    headers: {
      "X-SOC-API-Version": "1",
      "X-Request-Id": "req-alpha-browser-001",
      "X-Trace-Id": "trace-alpha-browser-001",
    },
    body: JSON.stringify(body),
  });
}

export async function mockSocAPI(
  page: Page,
  options: MockSocApiOptions = {},
): Promise<MockSocApiState> {
  const state: MockSocApiState = {
    requests: [],
    queueStatus: options.queueStatus ?? "open",
    candidateStatus: options.candidateStatus ?? "pending_review",
    normalizationStatus: "open",
    includeQueueItem: options.includeQueueItem ?? true,
    standaloneMemoryCandidate: options.standaloneMemoryCandidate ?? false,
  };

  await page.route("**/api/soc/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    let body: unknown = null;
    if (!["GET", "HEAD"].includes(method)) {
      try {
        body = request.postDataJSON();
      } catch {
        body = request.postData();
      }
    }
    state.requests.push({
      method,
      path,
      body,
      idempotencyKey: request.headers()["idempotency-key"] ?? null,
    });

    if (method === "GET" && path === "/api/soc/alerts") {
      return fulfill(route, { items: [alertResult(state)] });
    }
    if (method === "GET" && path === "/api/soc/alerts/RUN-ALPHA-001/context") {
      return fulfill(route, alertInvestigationContext(state));
    }

    if (method === "GET" && path === "/api/soc/review/items") {
      const requestedStatus = url.searchParams.get("status");
      const items =
        !state.includeQueueItem ||
        (requestedStatus && requestedStatus !== state.queueStatus)
          ? []
          : [queueItem(state)];
      return fulfill(route, { items });
    }
    if (
      method === "GET" &&
      path === "/api/soc/review/items/REV-ALPHA-001/context"
    ) {
      return fulfill(route, investigationContext(state));
    }
    if (
      method === "POST" &&
      path === "/api/soc/review/items/REV-ALPHA-001/close"
    ) {
      state.queueStatus = "closed";
      return fulfill(route, queueItem(state));
    }
    if (
      method === "POST" &&
      path === "/api/soc/review/runs/RUN-ALPHA-001/correct"
    ) {
      state.queueStatus = "closed";
      return fulfill(route, {
        ...investigationContext(state).run,
        decision: { verdict: "false_positive", confidence: 1 },
      });
    }
    if (method === "POST" && path === "/api/soc/review/disposition-outcomes") {
      return fulfill(route, {
        schema_version: "soc.disposition_outcome_apply_result.v1",
        outcome: {
          outcome_id: "DOUT-ALPHA-001",
          proposal_id: "DPROP-ALPHA-001",
        },
        idempotent: false,
        event_written: true,
      });
    }
    if (method === "GET" && path === "/api/soc/review/disposition-samples") {
      return fulfill(route, {
        schema_version: "soc.disposition_sample_manifest_list.v1",
        items: [sampleManifest()],
        has_more: false,
      });
    }
    if (
      method === "GET" &&
      path === "/api/soc/review/disposition-samples/DSAMPLE-ALPHA-001/inbox"
    ) {
      return fulfill(route, {
        schema_version: "soc.disposition_sample_review_inbox.v1",
        manifest: sampleManifest(),
        reviewer_actor_id: "default",
        total_count: 1,
        completed_count: 0,
        remaining_count: 1,
        reviewer_conflict_count: 0,
        completion_rate: 0,
        offset: 0,
        limit: 25,
        has_more: false,
        items: [
          {
            schema_version: "soc.disposition_sample_review_item.v1",
            sample_id: "DSAMPLE-ALPHA-001",
            selection_rank: 1,
            proposal_id: "DPROP-ALPHA-001",
            proposal: dispositionProposal(),
            queue_item: { ...queueItem(state), status: "closed" },
            primary_outcome: null,
            sampled_outcome: null,
            sampled_outcome_independent: null,
            reviewer_independent: true,
            readiness: "ready",
            can_record_outcome: true,
            blocking_reasons: [],
            auto_close_allowed: false,
            decision_impact: "none",
          },
        ],
        auto_close_allowed: false,
        decision_impact: "none",
      });
    }
    if (method === "GET" && path === "/api/soc/memory/records") {
      return fulfill(route, {
        items:
          state.candidateStatus === "confirmed" ? [memoryRecord(state)] : [],
      });
    }
    if (method === "GET" && path === "/api/soc/memory/candidates") {
      const requestedStatus = url.searchParams.get("status");
      const candidate = memoryCandidate(state);
      return fulfill(route, {
        items:
          requestedStatus && requestedStatus !== candidate.status
            ? []
            : [candidate],
      });
    }
    if (
      method === "GET" &&
      path === "/api/soc/memory/candidates/MC-ALPHA-001"
    ) {
      return fulfill(route, memoryCandidate(state));
    }
    if (
      method === "POST" &&
      path === "/api/soc/memory/candidates/MC-ALPHA-001/lesson-draft"
    ) {
      return fulfill(route, {
        schema_version: "soc.memory_business_lesson_draft.v1",
        candidate_id: "MC-ALPHA-001",
        reviewer_verdict: "false_positive",
        lesson: {
          schema_version: "soc.memory_business_lesson.v1",
          conclusion:
            "该模式是已确认的内部服务调用，应按审核范围复用误报结论。",
          business_rationale: [
            "运营专家已核对当前告警证据和内部服务登记信息。",
          ],
          applicability_conditions: [
            "Required canonical facet detection_key: pingan:ndr:reverse-shell",
            "Required canonical facet behavior_fingerprint: behavior-alpha",
            "Required canonical facet environment: prd",
          ],
          generalization_boundaries: ["审核范围未约束的源和目的 IP 可以变化。"],
          invalidation_conditions: [
            "必需 facet 不匹配或当前证据出现反证时失效。",
          ],
          handling_guidance: ["全部适用条件命中时复用误报结论，否则重新研判。"],
        },
        supporting_source_refs: ["D-001", "D-002"],
        rationale_sources: [
          {
            schema_version: "soc.memory_business_lesson_draft_rationale.v1",
            statement: "运营专家已核对当前告警证据和内部服务登记信息。",
            source_refs: ["D-001", "D-002"],
          },
        ],
        source_catalog: [
          {
            schema_version: "soc.memory_lesson_draft_source.v1",
            source_ref: "D-001",
            source_kind: "candidate",
            label: "candidate_summary",
            value: "Authorized scanner pattern",
          },
          {
            schema_version: "soc.memory_lesson_draft_source.v1",
            source_ref: "D-002",
            source_kind: "reviewer_verdict",
            label: "reviewer_selected_verdict",
            value: "false_positive",
          },
        ],
        uncertainties: [],
        provenance: {
          schema_version: "soc.memory_business_lesson_draft_provenance.v1",
          generator_id: "bounded-memory-business-lesson-drafter",
          model_name: "fixture-lesson-model",
          prompt_version: "soc-memory-business-lesson-draft-v3",
          prompt_hash: "a".repeat(64),
          response_hash: "b".repeat(64),
          repair_applied: false,
          repair_actions: [],
          repair_prompt_hash: null,
          provider_call_count: 1,
          output_repair_call_count: 0,
          usage: { input_tokens: 100, output_tokens: 80 },
          metadata: { thinking_enabled_requested: false },
        },
        decision_impact: "none",
        review_required: true,
        persistence_performed: false,
        generated_at: NOW,
      });
    }
    if (
      method === "POST" &&
      path === "/api/soc/memory/candidates/MC-ALPHA-001/review"
    ) {
      const decision =
        typeof body === "object" && body !== null && "decision" in body
          ? String(body.decision)
          : "confirm";
      const previousStatus = state.candidateStatus;
      if (decision === "reopen") {
        state.candidateStatus = "pending_review";
      } else if (decision === "reject") {
        state.candidateStatus = "rejected";
      } else if (decision === "confirm_candidate") {
        state.candidateStatus = "confirmed_candidate";
      } else if (decision === "expire") {
        state.candidateStatus = "expired";
      } else if (decision === "deprecate") {
        state.candidateStatus = "deprecated";
      } else {
        state.candidateStatus = "confirmed";
      }
      return fulfill(route, {
        schema_version: "soc.memory_candidate_review_result.v1",
        candidate: memoryCandidate(state),
        memory_record: decision === "confirm" ? memoryRecord(state) : null,
        previous_status: previousStatus,
        decision,
        reviewed_at: NOW,
      });
    }
    if (
      method === "POST" &&
      path === "/api/soc/memory/records/MEM-ALPHA-001/retrieval"
    ) {
      return fulfill(route, {
        schema_version: "soc.memory_retrieval_activation_result.v1",
        record: {
          ...memoryRecord(state),
          version: 2,
          retrieval_enabled: true,
          retrieval_policy_version: "soc.memory_retrieval_activation_policy.v1",
        },
        action: "enable",
        previous_record_version: 1,
        previous_retrieval_enabled: false,
        audit_id: "MUTA-ALPHA-001",
        policy_version: "soc.memory_retrieval_activation_policy.v1",
        changed_at: NOW,
      });
    }
    if (method === "GET" && path === "/api/soc/approvals/requests") {
      return fulfill(route, { items: [approvalRequest()] });
    }
    if (
      method === "GET" &&
      path === "/api/soc/approvals/requests/APR-ALPHA-001"
    ) {
      return fulfill(route, approvalRequest());
    }
    if (method === "POST" && path === "/api/soc/approvals/grants") {
      return fulfill(route, {
        schema_version: "soc.agent_approval_grant.v1",
        approval_grant_id: "APG-ALPHA-001",
        execution_token_id: "SAT-ALPHA-001",
        approval_request_id: "APR-ALPHA-001",
        permission_decision_id: "PERM-ALPHA-001",
        route: "response.block_ip",
        action: "response.block_ip",
        risk_level: "high_risk",
        requested_by: approvalRequest().requested_by,
        approved_by: { actor_id: "default", surface: "web" },
        approval_reason: "Approved for bounded Alpha regression",
        status: "approved",
        single_use: true,
        approved_at: NOW,
        expires_at: "2026-07-20T08:15:00Z",
        policy_version: "soc.approval_policy.v1",
      });
    }
    if (method === "POST" && path === "/api/soc/approvals/actions/dry-run") {
      return fulfill(route, {
        schema_version: "soc.agent_action_result.v1",
        route: "response.block_ip",
        action: "response.block_ip",
        status: "success",
        message: "Dry-run validated without side effects.",
        payload: { external_side_effect: "not_executed" },
      });
    }
    if (method === "GET" && path === "/api/soc/normalization/issues") {
      return fulfill(route, { items: [normalizationIssue(state)] });
    }
    if (method === "GET" && path === "/api/soc/operations/snapshot") {
      return fulfill(route, operationsSnapshot());
    }
    if (method === "GET" && path === "/api/soc/effectiveness/snapshot") {
      return fulfill(route, effectivenessSnapshot());
    }
    if (
      method === "GET" &&
      path === "/api/soc/effectiveness/rules/0123456789abcdef"
    ) {
      return fulfill(route, ruleEffectivenessDetail());
    }
    if (method === "GET" && path === "/api/soc/normalization/baselines") {
      return fulfill(route, {
        items: [
          {
            schema_version: "soc.normalization_schema_baseline.v1",
            baseline_id: "BASE-ALPHA-001",
            version: 1,
            status: "active",
            tenant_id: "tenant-alpha",
            source_system: "alpha-fixture",
            adapter: "pingan_platform",
            parser_name: "zeus-message",
            parser_version: "v1",
            accepted_fingerprints: ["schema-alpha-001"],
            reason: "Alpha fixture baseline",
            created_at: NOW,
            updated_at: NOW,
          },
        ],
      });
    }
    if (method === "GET" && path === "/api/soc/normalization/metrics") {
      return fulfill(route, {
        schema_version: "soc.normalization_operations_metrics.v1",
        open_issue_count: state.normalizationStatus === "open" ? 1 : 0,
        issue_type_counts: { novel_schema: 1 },
        severity_counts: { warning: 1 },
        source_system_counts: { "alpha-fixture": 1 },
        active_baseline_count: 1,
      });
    }
    if (
      method === "PATCH" &&
      path === "/api/soc/normalization/issues/NORM-ALPHA-001"
    ) {
      const update = body as { status?: string };
      state.normalizationStatus = update.status ?? state.normalizationStatus;
      return fulfill(route, normalizationIssue(state));
    }

    return fulfill(
      route,
      {
        schema_version: "soc.api.problem.v1",
        code: "soc.test_route_missing",
        detail: `Unhandled SOC browser fixture route: ${method} ${path}`,
        retryable: false,
      },
      404,
    );
  });

  return state;
}
