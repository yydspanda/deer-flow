export type SocReviewQueueStatus = "open" | "closed";

export type SocReviewQueuePriority = "low" | "medium" | "high";

export type SocVerdict =
  | "true_positive"
  | "suspicious"
  | "false_positive"
  | "unknown"
  | "needs_review";

export type SocDecisionConfidenceSource =
  | "unknown"
  | "stub_heuristic"
  | "llm_self_report"
  | "human_confirmation"
  | "external_disposition";

export type SocEntrySurface = "api" | "web";

export interface SocRequestContext {
  actorId?: string;
  surface?: SocEntrySurface;
  requestId?: string;
  traceId?: string;
  idempotencyKey?: string;
}

export interface SocActorContext {
  actor_id: string;
  actor_type?: string;
  surface: string;
  roles?: string[];
  auth_source?: string;
}

export interface SocReviewQueueItem {
  schema_version: string;
  queue_id: string;
  run_id: string;
  alert_id: string;
  tenant_id?: string | null;
  status: SocReviewQueueStatus;
  priority: SocReviewQueuePriority;
  reason: string;
  source_type: string;
  source_system?: string | null;
  rule_code?: string | null;
  rule_name?: string | null;
  severity?: string | null;
  category?: string | null;
  verdict?: SocVerdict | null;
  confidence?: number | null;
  entity_keys: string[];
  summary?: string | null;
  created_at: string;
  updated_at: string;
  closed_at?: string | null;
  closed_by?: SocActorContext | null;
  close_reason?: string | null;
}

export interface SocReviewQueueListResponse {
  items: SocReviewQueueItem[];
}

export interface SocAnalysisRun {
  run_id: string;
  alert_id: string;
  status: string;
  pipeline_version: string;
  model_name: string;
  prompt_version: string;
  input_payload?: Record<string, unknown> | null;
  input_hash?: string | null;
  started_at: string;
  ended_at?: string | null;
  request_journal?: {
    schema_version: string;
    status: "running" | "completed" | "failed" | "interrupted";
    action: "analysis" | "replay";
    request_id: string;
    trace_id?: string | null;
    request_hash: string;
    model_name: string;
    prompt_version: string;
    provider_step_name: string;
    provider_started_at: string;
    finalized_at?: string | null;
    failure_kind?: string | null;
    failure_retryable?: boolean | null;
    recovery_run_id?: string | null;
  } | null;
  entities?: Record<string, unknown> | null;
  normalization_report?: Record<string, unknown> | null;
  extraction_report?: Record<string, unknown> | null;
  fact_reconstruction?: Record<string, unknown> | null;
  analysis?: Record<string, unknown> | null;
  decision?: Record<string, unknown> | null;
  corrections?: Record<string, unknown>[];
}

export interface SocAlertSummary {
  run_id: string;
  alert_id: string;
  tenant_id?: string | null;
  detection_key?: string | null;
  source_type?: string | null;
  source_system?: string | null;
  rule_code?: string | null;
  rule_name?: string | null;
  severity?: string | null;
  category?: string | null;
  verdict?: SocVerdict | null;
  confidence?: number | null;
  confidence_source?: SocDecisionConfidenceSource | null;
  confidence_is_calibrated?: boolean;
  confidence_policy_version?: string | null;
  confidence_explanation?: string | null;
  entity_keys?: string[];
  summary?: string | null;
  recommended_action?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface SocDecisionAuditRecord {
  audit_id?: string;
  run_id?: string;
  action?: string;
  actor?: SocActorContext | null;
  created_at?: string;
  payload?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SocSimilarAlertMatch {
  summary: SocAlertSummary;
  score: number;
  matched_reasons: string[];
}

export interface SocCorrelationEvidenceRef {
  evidence_id: string;
  route: string;
  action: string;
  status: "success" | "denied" | "failed";
  message: string;
  result_payload: Record<string, unknown>;
  queue_id?: string | null;
  run_id?: string | null;
  alert_id?: string | null;
  source_proposal_id?: string | null;
  created_at: string;
}

export interface SocCorrelationMatch {
  summary: SocAlertSummary;
  score: number;
  matched_reasons: string[];
  reusable_evidence: SocCorrelationEvidenceRef[];
}

export interface SocCorrelationResult {
  schema_version: string;
  query: Record<string, unknown>;
  subject_summary: SocAlertSummary;
  matches: SocCorrelationMatch[];
  reusable_evidence_count: number;
  generated_at: string;
}

export type SocDomainName = "apt" | "edr" | "hids" | "waf_f5" | "generic";

export type SocDomainFindingSeverity =
  | "info"
  | "low"
  | "medium"
  | "high"
  | "critical";

export type SocDomainFindingDisposition =
  | "suspicious"
  | "likely_true_positive"
  | "likely_false_positive"
  | "benign_authorized_candidate"
  | "needs_more_evidence";

export interface SocEvidenceProfile {
  schema_version: string;
  sources: Record<string, string>;
  used_sources: string[];
  gaps: string[];
  notes: string[];
}

export interface SocFindingConclusion {
  schema_version: string;
  summary: string;
  risk_level: SocDomainFindingSeverity;
  certainty: "low" | "medium_low" | "medium" | "medium_high" | "high";
  recommended_action: string;
  recommended_queue?: string | null;
  automation_allowed: boolean;
  rationale: string[];
}

export interface SocDomainFinding {
  schema_version: string;
  finding_id: string;
  domain: SocDomainName;
  scenario_key?: string | null;
  scenario_name?: string | null;
  vendor_scenarios: string[];
  title: string;
  summary: string;
  severity: SocDomainFindingSeverity;
  disposition: SocDomainFindingDisposition;
  confidence: number;
  evidence_profile: SocEvidenceProfile;
  current_conclusion: SocFindingConclusion;
  evidence_refs: string[];
  capability_card_refs: string[];
  skill_names: string[];
  recommendations: string[];
  limitations: string[];
  human_checklist: string[];
  metadata: Record<string, unknown>;
}

export interface SocDomainTriageResult {
  schema_version: string;
  request_id: string;
  run_id: string;
  alert_id: string;
  domain: SocDomainName;
  handler_id: string;
  findings: SocDomainFinding[];
  evidence_ref_count: number;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface SocInvestigationEvidence {
  schema_version: string;
  evidence_id: string;
  source_type: "read_only_action_result";
  route: string;
  action: string;
  status: "success" | "denied" | "failed";
  message: string;
  result_payload: Record<string, unknown>;
  queue_id?: string | null;
  run_id?: string | null;
  alert_id?: string | null;
  thread_id?: string | null;
  source_proposal_id?: string | null;
  context_hash?: string | null;
  actor?: SocActorContext | null;
  created_at: string;
}

export type SocOperationalDisposition =
  | "closed_true_positive"
  | "closed_false_positive"
  | "closed_benign_true_positive"
  | "suppressed"
  | "escalated"
  | "ignored"
  | "duplicate"
  | "unknown";

export type SocExternalDispositionCanonicalStatus = SocOperationalDisposition;

export type SocExternalDispositionApplyStatus =
  | "mapped"
  | "unmatched"
  | "ignored";

export interface SocExternalDispositionEvent {
  schema_version: string;
  tenant_id?: string | null;
  external_system: string;
  external_case_id: string;
  source_event_id?: string | null;
  source_version?: string | null;
  external_alert_ref?: string | null;
  soc_alert_id?: string | null;
  soc_run_id?: string | null;
  soc_queue_id?: string | null;
  external_status: string;
  external_reason?: string | null;
  external_tags: string[];
  operator: Record<string, unknown>;
  updated_at: string;
  raw_payload_hash: string;
  metadata: Record<string, unknown>;
}

export interface SocExternalDispositionRecord {
  schema_version: string;
  disposition_id: string;
  event: SocExternalDispositionEvent;
  canonical_status: SocExternalDispositionCanonicalStatus;
  apply_status: SocExternalDispositionApplyStatus;
  idempotency_key: string;
  target_run_id?: string | null;
  target_alert_id?: string | null;
  target_queue_id?: string | null;
  matched_by?: string | null;
  apply_reason: string;
  audit_id?: string | null;
  correction_id?: string | null;
  memory_candidate_id?: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export type SocMemoryCandidateStatus =
  | "pending_review"
  | "confirmed_candidate"
  | "confirmed"
  | "rejected"
  | "deprecated"
  | "expired";

export type SocMemoryCandidateReviewDecision =
  | "confirm_candidate"
  | "confirm"
  | "reject"
  | "deprecate"
  | "expire";

export type SocMemoryRecordStatus = "confirmed" | "deprecated" | "expired";

export type SocMemoryRetrievalActivationAction = "enable" | "disable";

export type SocMemoryCandidateType =
  | "procedure"
  | "detection_lesson"
  | "benign_pattern"
  | "environment_fact"
  | "identity_pattern"
  | "response_policy_hint"
  | "negative_memory"
  | "adapter_mapping"
  | "eval_fixture";

export type SocMemoryTargetArtifact =
  | "public_skill"
  | "tenant_memory"
  | "adapter_mapping"
  | "policy_config"
  | "normalizer"
  | "domain_handler"
  | "eval_fixture"
  | "prompt_context"
  | "external_sync";

export interface SocMemoryCandidateSource {
  source_type: string;
  source_surface?: string | null;
  source_id?: string | null;
  source_doc?: string | null;
  source_section?: string | null;
  capability_card_id?: string | null;
  run_id?: string | null;
  alert_id?: string | null;
  queue_id?: string | null;
  correction_id?: string | null;
  eval_sample_id?: string | null;
  metadata: Record<string, unknown>;
}

export interface SocMemoryCandidateValidity {
  valid_from: string;
  valid_until?: string | null;
  review_after_days?: number | null;
  notes: string;
}

export interface SocMemoryCandidate {
  schema_version: string;
  candidate_id: string;
  candidate_type: SocMemoryCandidateType;
  target_artifact: SocMemoryTargetArtifact;
  summary: string;
  content: string;
  tenant_scope: string;
  tenant_id?: string | null;
  status: SocMemoryCandidateStatus;
  source: SocMemoryCandidateSource;
  evidence_refs: string[];
  validity: SocMemoryCandidateValidity;
  idempotency_key?: string | null;
  confidence: number;
  facets: Record<string, string[]>;
  decision_impact: string;
  runtime_decision_allowed: false;
  review_required: true;
  review_owner?: string | null;
  reviewed_by?: SocActorContext | null;
  reviewed_at?: string | null;
  review_reason?: string | null;
  labels: string[];
  metadata: Record<string, unknown>;
  proposed_by?: SocActorContext | null;
  created_at: string;
  updated_at: string;
}

export interface SocMemoryCandidateListResponse {
  items: SocMemoryCandidate[];
}

export interface SocMemoryRecord {
  schema_version: string;
  memory_id: string;
  version: number;
  memory_type: SocMemoryCandidateType;
  target_artifact: SocMemoryTargetArtifact;
  status: SocMemoryRecordStatus;
  tenant_scope: string;
  tenant_id?: string | null;
  source_candidate_id: string;
  source: SocMemoryCandidateSource;
  summary: string;
  content: string;
  facets: Record<string, string[]>;
  evidence_refs: string[];
  validity: SocMemoryCandidateValidity;
  confidence: number;
  decision_impact: string;
  content_hash: string;
  facets_hash: string;
  retrieval_enabled: boolean;
  retrieval_policy_version?: string | null;
  retrieval_valid_until?: string | null;
  retrieval_review_due_at?: string | null;
  retrieval_updated_by?: SocActorContext | null;
  retrieval_updated_at?: string | null;
  retrieval_reason?: string | null;
  created_by: SocActorContext;
  created_at: string;
  updated_at: string;
  deprecated_by?: SocActorContext | null;
  deprecated_at?: string | null;
  deprecation_reason?: string | null;
  labels: string[];
  metadata: Record<string, unknown>;
}

export interface SocMemoryRecordListResponse {
  items: SocMemoryRecord[];
}

export interface SocMemoryRetrievalActivationRequest {
  action: SocMemoryRetrievalActivationAction;
  expected_record_version: number;
  reason: string;
  activation_valid_until?: string | null;
  review_after_days?: number | null;
  metadata?: Record<string, unknown>;
}

export interface SocMemoryRetrievalActivationResult {
  schema_version: string;
  record: SocMemoryRecord;
  action: SocMemoryRetrievalActivationAction;
  previous_record_version: number;
  previous_retrieval_enabled: boolean;
  audit_id?: string | null;
  policy_version: string;
  changed_at: string;
}

export interface SocMemoryQuery {
  schema_version?: string;
  memory_types?: SocMemoryCandidateType[];
  statuses?: SocMemoryRecordStatus[];
  tenant_scope?: string | null;
  tenant_id?: string | null;
  facets?: Record<string, string[]>;
  text_terms?: string[];
  evidence_refs?: string[];
  limit?: number;
  candidate_limit?: number;
  min_score?: number;
  max_tokens?: number;
  require_retrieval_enabled?: true;
  metadata?: Record<string, unknown>;
}

export interface SocMemoryMatch {
  memory_id: string;
  version: number;
  record: SocMemoryRecord;
  score: number;
  match_reasons: string[];
  matched_facets: Record<string, string[]>;
  token_estimate: number;
  content_hash: string;
  facets_hash: string;
  retrieval_enabled: true;
}

export interface SocMemoryRetrievalResult {
  schema_version: string;
  policy_version: string;
  query: SocMemoryQuery;
  matches: SocMemoryMatch[];
  total_candidate_count: number;
  skipped_retrieval_disabled: number;
  skipped_ungoverned_activation: number;
  skipped_activation_expired: number;
  skipped_review_overdue: number;
  skipped_status: number;
  skipped_expired: number;
  skipped_below_min_score: number;
  returned_count: number;
  total_token_estimate: number;
  max_tokens: number;
  replay_diff?: SocMemoryRetrievalDiff | null;
  created_at: string;
}

export interface SocMemoryRetrievalDiff {
  schema_version: string;
  baseline_policy_version: string;
  current_policy_version: string;
  added_memory_ids: string[];
  removed_memory_ids: string[];
  changed_memory_ids: string[];
  unchanged_memory_ids: string[];
  changed: boolean;
}

export type SocInvestigationTimelineKind =
  | "analysis"
  | "decision"
  | "correlation"
  | "domain_finding"
  | "read_only_evidence"
  | "authorization_enrichment"
  | "disposition_proposal"
  | "disposition_outcome"
  | "external_disposition"
  | "memory_candidate"
  | "relevant_memory"
  | "audit"
  | "correction";

export interface SocInvestigationTimelineItem {
  schema_version: string;
  item_id: string;
  kind: SocInvestigationTimelineKind;
  title: string;
  summary?: string | null;
  status?: string | null;
  severity?: string | null;
  source_id?: string | null;
  source_refs: Record<string, string>;
  occurred_at?: string | null;
  payload: Record<string, unknown>;
}

export interface SocUnifiedInvestigationView {
  schema_version: string;
  view_id: string;
  queue_id: string;
  run_id: string;
  alert_id: string;
  generated_at: string;
  runtime_verdict?: SocVerdict | null;
  runtime_confidence?: number | null;
  needs_review: boolean;
  automation_allowed: boolean;
  primary_summary?: string | null;
  primary_reason?: string | null;
  correlation_result?: SocCorrelationResult | null;
  domain_triage_results: SocDomainTriageResult[];
  evidence_timeline: SocInvestigationTimelineItem[];
  counts: Record<string, number>;
  boundary_notes: string[];
  metadata: Record<string, unknown>;
}

export interface SocAuthorizationFactRef {
  fact_id: string;
  fact_version_id: string;
  version: number;
  status: string;
  content_hash: string;
}

export interface SocAuthorizationQuery {
  schema_version: string;
  query_id: string;
  alert_id: string;
  tenant_id?: string | null;
  environment?: string | null;
  event_time?: string | null;
  unresolved_event_time?: string | null;
  subjects: Record<string, unknown>[];
  targets: Record<string, unknown>[];
  behaviors: Record<string, unknown>[];
  conflicts: Record<string, unknown>[];
  warnings: string[];
}

export interface SocAuthorizationMatchResult {
  schema_version: string;
  query_id: string;
  alert_id: string;
  status:
    | "exact"
    | "partial"
    | "conflict"
    | "expired"
    | "not_found"
    | "unavailable";
  event_time?: string | null;
  policy_version: string;
  matched_fact_refs: SocAuthorizationFactRef[];
  candidate_fact_refs: SocAuthorizationFactRef[];
  matched_dimensions: string[];
  missing_dimensions: string[];
  out_of_scope_dimensions: string[];
  source_freshness: string[];
  evidence_refs: string[];
  fact_evaluations: Record<string, unknown>[];
  warnings: string[];
  shadow_only: true;
}

export interface SocAuthorizationEnrichmentRecord {
  schema_version: string;
  enrichment_id: string;
  run_id: string;
  alert_id: string;
  queue_id?: string | null;
  query: SocAuthorizationQuery;
  query_hash: string;
  match_result: SocAuthorizationMatchResult;
  matcher_policy_version: string;
  idempotency_key: string;
  replay_of_enrichment_id?: string | null;
  created_by: SocActorContext;
  created_at: string;
  shadow_only: true;
  decision_impact: "none";
}

export interface SocDetectionTruthSnapshot {
  schema_version: string;
  verdict: SocVerdict;
  confidence?: number | null;
  source: "decision" | "analysis";
  decision_policy_version?: string | null;
  latest_correction_id?: string | null;
}

export interface SocDispositionProposalRecord {
  schema_version: string;
  proposal_id: string;
  proposal_key: string;
  run_id: string;
  alert_id: string;
  queue_id: string;
  source_enrichment_id: string;
  source_query_hash: string;
  source_matcher_policy_version: string;
  source_fact_refs: SocAuthorizationFactRef[];
  source_evidence_refs: string[];
  detection_truth: SocDetectionTruthSnapshot;
  proposed_disposition: SocOperationalDisposition;
  reason_code: "authorized_activity_exact_match";
  rationale: string[];
  policy_version: string;
  idempotency_key: string;
  created_by: SocActorContext;
  created_at: string;
  proposal_mode: "shadow";
  application_status: "not_applied";
  requires_human_review: true;
  auto_close_allowed: false;
  detection_truth_impact: "none";
  review_queue_impact: "none";
}

export interface SocDispositionEvaluationScope {
  schema_version: string;
  tenant_id?: string | null;
  environment?: string | null;
  window_start: string;
  window_end: string;
  proposal_policy_version: string;
  matcher_policy_version: string;
}

export interface SocDispositionSampleManifest {
  schema_version: string;
  sample_id: string;
  sample_key: string;
  scope: SocDispositionEvaluationScope;
  scope_hash: string;
  population_count: number;
  population_hash: string;
  selected_proposal_ids: string[];
  sample_size: number;
  selection_seed_hash: string;
  sampling_method: "sha256_rank_v1";
  idempotency_key: string;
  created_by: SocActorContext;
  created_at: string;
  shadow_only: true;
  decision_impact: "none";
}

export interface SocDispositionSampleManifestListResponse {
  schema_version: string;
  items: SocDispositionSampleManifest[];
  limit: number;
  has_more: boolean;
}

export interface SocDispositionOutcomeRecord {
  schema_version: string;
  outcome_id: string;
  lineage_key: string;
  proposal_id: string;
  proposal_key: string;
  run_id: string;
  alert_id: string;
  queue_id: string;
  proposed_disposition: SocOperationalDisposition;
  observed_disposition: SocOperationalDisposition;
  outcome_status: "confirmed" | "overridden" | "inconclusive";
  review_kind: "analyst_resolution" | "sampled_quality_review";
  source: "analyst" | "external_disposition" | "replay_label";
  source_ref?: string | null;
  sample_id?: string | null;
  reason: string;
  evidence_refs: string[];
  proposal_policy_version: string;
  supersedes_outcome_id?: string | null;
  idempotency_key: string;
  reviewed_by: SocActorContext;
  observed_at: string;
  created_at: string;
  shadow_only: true;
  decision_impact: "none";
  review_queue_impact: "none";
}

export type SocDispositionOutcomeReviewKind =
  | "analyst_resolution"
  | "sampled_quality_review";

export interface SocDispositionOutcomeRecordRequest {
  proposal_id: string;
  observed_disposition: SocOperationalDisposition;
  review_kind: SocDispositionOutcomeReviewKind;
  sample_id?: string | null;
  reason: string;
  evidence_refs?: string[];
  observed_at?: string | null;
  supersedes_outcome_id?: string | null;
}

export interface SocDispositionOutcomeApplyResult {
  schema_version: string;
  outcome: SocDispositionOutcomeRecord;
  idempotent: boolean;
  event_written: boolean;
}

export type SocDispositionSampleReviewReadiness =
  | "ready"
  | "waiting_for_queue_close"
  | "completed"
  | "unavailable";

export interface SocDispositionSampleReviewItem {
  schema_version: string;
  sample_id: string;
  selection_rank: number;
  proposal_id: string;
  proposal?: SocDispositionProposalRecord | null;
  queue_item?: SocReviewQueueItem | null;
  primary_outcome?: SocDispositionOutcomeRecord | null;
  sampled_outcome?: SocDispositionOutcomeRecord | null;
  sampled_outcome_independent?: boolean | null;
  reviewer_independent?: boolean | null;
  readiness: SocDispositionSampleReviewReadiness;
  can_record_outcome: boolean;
  blocking_reasons: string[];
  auto_close_allowed: false;
  decision_impact: "none";
}

export interface SocDispositionSampleReviewInbox {
  schema_version: string;
  manifest: SocDispositionSampleManifest;
  reviewer_actor_id: string;
  total_count: number;
  completed_count: number;
  remaining_count: number;
  reviewer_conflict_count: number;
  completion_rate: number;
  offset: number;
  limit: number;
  has_more: boolean;
  items: SocDispositionSampleReviewItem[];
  auto_close_allowed: false;
  decision_impact: "none";
}

export interface SocMemoryCandidateReviewRequest {
  decision: SocMemoryCandidateReviewDecision;
  reason: string;
  record_summary?: string | null;
  record_content?: string | null;
  metadata?: Record<string, unknown>;
}

export interface SocMemoryCandidateReviewResult {
  schema_version: string;
  candidate: SocMemoryCandidate;
  memory_record?: SocMemoryRecord | null;
  previous_status: SocMemoryCandidateStatus;
  decision: SocMemoryCandidateReviewDecision;
  reviewed_at: string;
}

export interface SocInvestigationContext {
  schema_version: string;
  queue_item: SocReviewQueueItem;
  run: SocAnalysisRun;
  summary?: SocAlertSummary | null;
  audit_records: SocDecisionAuditRecord[];
  similar_alerts: SocSimilarAlertMatch[];
  action_evidence: SocInvestigationEvidence[];
  authorization_enrichments: SocAuthorizationEnrichmentRecord[];
  disposition_proposals: SocDispositionProposalRecord[];
  disposition_outcomes: SocDispositionOutcomeRecord[];
  external_dispositions: SocExternalDispositionRecord[];
  memory_candidates: SocMemoryCandidate[];
  relevant_memories?: SocMemoryRetrievalResult | null;
  correlation_result?: SocCorrelationResult | null;
  domain_triage_results?: SocDomainTriageResult[];
  investigation_view?: SocUnifiedInvestigationView | null;
}

export interface SocReviewCloseRequest {
  reason: string;
}

export interface SocReviewCorrectionRequest {
  verdict: SocVerdict;
  reason: string;
  confidence?: number | null;
}

export type SocAgentRiskLevel =
  | "read_only"
  | "analyst_write"
  | "high_risk"
  | "unknown";

export type SocAgentApprovalRequestStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired";

export interface SocAgentApprovalRequest {
  schema_version?: "soc.agent_approval_request.v1";
  approval_request_id?: string;
  permission_decision_id: string;
  route: string;
  action: string;
  risk_level: SocAgentRiskLevel;
  reason: string;
  requested_by: SocActorContext;
  submitted_by?: SocActorContext | null;
  source_proposal_id?: string | null;
  action_payload?: Record<string, unknown>;
  context_refs?: Record<string, unknown>;
  status: SocAgentApprovalRequestStatus;
  created_at?: string;
  resolved_at?: string | null;
  resolved_by?: SocActorContext | null;
  resolution_reason?: string | null;
  resolution_idempotency_key?: string | null;
  resolution_expires_in_seconds?: number | null;
  approval_grant_id?: string | null;
}

export interface SocApprovalRequestListResponse {
  items: SocAgentApprovalRequest[];
}

export interface SocAgentApprovalGrant {
  schema_version: "soc.agent_approval_grant.v1";
  approval_grant_id: string;
  execution_token_id: string;
  approval_request_id: string;
  permission_decision_id: string;
  route: string;
  action: string;
  risk_level: SocAgentRiskLevel;
  requested_by: SocActorContext;
  approved_by: SocActorContext;
  approval_reason: string;
  idempotency_key?: string | null;
  status: "approved" | "consumed";
  single_use: true;
  approved_at: string;
  expires_at: string;
  consumed_at?: string | null;
  consumed_by?: SocActorContext | null;
  consume_idempotency_key?: string | null;
  execution_result_id?: string | null;
  execution_result_payload?: Record<string, unknown> | null;
  policy_version: string;
}

export interface SocApprovalGrantRequest {
  approval_request_id: string;
  reason: string;
  expires_in_seconds?: number;
}

export interface SocApprovalResolutionRequest {
  reason: string;
}

export interface SocAgentApprovedActionCommand {
  schema_version?: "soc.agent_approved_action_command.v1";
  execution_token_id: string;
  route: string;
  action: string;
  dry_run?: boolean;
  payload?: Record<string, unknown>;
}

export interface SocAgentActionResult {
  schema_version: "soc.agent_action_result.v1";
  route: string;
  action: string;
  status: "success" | "denied" | "failed";
  message: string;
  payload: Record<string, unknown>;
  requires_human_approval?: boolean;
}

export type SocNormalizationIssueStatus =
  | "open"
  | "acknowledged"
  | "resolved"
  | "ignored";

export type SocNormalizationIssueType =
  | "baseline_missing"
  | "novel_schema"
  | "degraded_schema"
  | "unsupported_schema"
  | "high_value_gap"
  | "evidence_truncated";

export interface SocNormalizationMaintenanceIssue {
  schema_version: "soc.normalization_maintenance_issue.v1";
  issue_id: string;
  dedupe_key: string;
  issue_type: SocNormalizationIssueType;
  severity: "info" | "warning" | "critical";
  status: SocNormalizationIssueStatus;
  tenant_id?: string | null;
  source_system?: string | null;
  adapter: string;
  parser_name?: string | null;
  parser_version?: string | null;
  schema_fingerprint?: string | null;
  source_path?: string | null;
  expected_target?: string | null;
  run_id?: string | null;
  alert_id?: string | null;
  occurrence_count: number;
  first_seen_at: string;
  last_seen_at: string;
  resolution_reason?: string | null;
  details: Record<string, unknown>;
}

export interface SocNormalizationIssueListResponse {
  items: SocNormalizationMaintenanceIssue[];
}

export interface SocNormalizationIssueUpdateRequest {
  status: Exclude<SocNormalizationIssueStatus, "open">;
  reason: string;
}

export interface SocNormalizationSchemaBaseline {
  schema_version: "soc.normalization_schema_baseline.v1";
  baseline_id: string;
  version: number;
  status: "active" | "superseded";
  tenant_id?: string | null;
  source_system?: string | null;
  adapter: string;
  parser_name: string;
  parser_version: string;
  accepted_fingerprints: string[];
  reason: string;
  created_at: string;
  updated_at: string;
}

export interface SocNormalizationBaselineListResponse {
  items: SocNormalizationSchemaBaseline[];
}

export interface SocNormalizationOperationsMetrics {
  schema_version: "soc.normalization_operations_metrics.v1";
  open_issue_count: number;
  issue_type_counts: Record<string, number>;
  severity_counts: Record<string, number>;
  source_system_counts: Record<string, number>;
  active_baseline_count: number;
}
