import type { Page, Route } from "@playwright/test";

const NOW = "2026-07-20T08:00:00Z";

export interface MockSocApiOptions {
  queueStatus?: "open" | "closed";
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
    reason: "LLM evidence requires analyst confirmation",
    source_type: "ndr",
    source_system: "alpha-fixture",
    rule_code: "APT-REVERSE-SHELL",
    rule_name: "Reverse shell activity",
    severity: "high",
    category: "command_and_control",
    verdict: "needs_review",
    confidence: 0.72,
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
      queue_id: "REV-ALPHA-001",
      metadata: {},
    },
    evidence_refs: ["review_queue:REV-ALPHA-001"],
    validity: {
      valid_from: NOW,
      valid_until: null,
      review_after_days: 30,
      notes: "Alpha browser fixture",
    },
    idempotency_key: "memory-candidate-alpha-001",
    confidence: 0.8,
    facets: { source_type: ["ndr"], scenario: ["reverse_shell"] },
    decision_impact: "none_until_confirmed",
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
      schema_version: "soc.memory_retrieval_result.v1",
      policy_version: "soc.memory_retrieval.v1",
      query: { require_retrieval_enabled: true },
      matches: [],
      total_candidate_count: 1,
      skipped_retrieval_disabled: 1,
      skipped_ungoverned_activation: 0,
      skipped_activation_expired: 0,
      skipped_review_overdue: 0,
      skipped_status: 0,
      skipped_expired: 0,
      skipped_below_min_score: 0,
      returned_count: 0,
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

export function mockSocAPI(
  page: Page,
  options: MockSocApiOptions = {},
): MockSocApiState {
  const state: MockSocApiState = {
    requests: [],
    queueStatus: options.queueStatus ?? "open",
    candidateStatus: "pending_review",
    normalizationStatus: "open",
  };

  void page.route("**/api/soc/**", async (route) => {
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

    if (method === "GET" && path === "/api/soc/review/items") {
      const requestedStatus = url.searchParams.get("status");
      const items =
        requestedStatus && requestedStatus !== state.queueStatus
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
      return fulfill(route, { items: [memoryRecord(state)] });
    }
    if (
      method === "POST" &&
      path === "/api/soc/memory/candidates/MC-ALPHA-001/review"
    ) {
      state.candidateStatus = "confirmed";
      return fulfill(route, {
        schema_version: "soc.memory_candidate_review_result.v1",
        candidate: memoryCandidate(state),
        memory_record: memoryRecord(state),
        previous_status: "pending_review",
        decision: "confirm",
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
