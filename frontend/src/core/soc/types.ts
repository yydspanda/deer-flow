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
  review_reasons?: string[];
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

export interface SocAnalysisRequestJournal {
  schema_version: string;
  status: "running" | "completed" | "failed" | "interrupted";
  action: "analysis" | "replay";
  request_id: string;
  trace_id?: string | null;
  request_hash: string;
  model_name: string;
  prompt_version: string;
  provider_step_name: string;
  provider_purpose:
    | "primary_analysis"
    | "primary_analysis_retry"
    | "primary_analysis_section_repair"
    | "role_verification"
    | "role_verification_retry";
  parser_version?: string | null;
  optional_provider: boolean;
  provider_started_at: string;
  finalized_at?: string | null;
  failure_kind?: string | null;
  failure_retryable?: boolean | null;
  recovery_run_id?: string | null;
}

export interface SocAnalysisOutputQuality {
  schema_version: "soc.analysis_output_quality.v1";
  status: "accepted" | "repaired" | "degraded" | "deterministic_fallback";
  accepted_sections: string[];
  degraded_sections: string[];
  repair_attempted: boolean;
  deterministic_fallback_used: boolean;
  issues: Array<{
    section: string;
    stage: string;
    error_type: string;
    attempt: number;
    field_paths: string[];
    issue_codes: string[];
  }>;
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
  total_duration_ms?: number | null;
  request_journal?: SocAnalysisRequestJournal | null;
  provider_request_journals?: SocAnalysisRequestJournal[];
  entities?: Record<string, unknown> | null;
  normalization_report?: Record<string, unknown> | null;
  extraction_report?: Record<string, unknown> | null;
  fact_reconstruction?: Record<string, unknown> | null;
  analysis?: Record<string, unknown> | null;
  analysis_output_quality?: SocAnalysisOutputQuality | null;
  decision?: Record<string, unknown> | null;
  corrections?: Record<string, unknown>[];
  role_adjudication_revisions?: Record<string, unknown>[];
  role_verification_trigger?: {
    schema_version: string;
    policy_version: string;
    triggered: boolean;
    reasons: string[];
    claim_count: number;
    claims_hash: string;
    minimum_confidence: number;
  } | null;
  role_adjudication_verification?: {
    schema_version: string;
    status: "confirmed" | "challenged" | "unresolved" | "unavailable";
    claims: Record<string, unknown>[];
    claim_reviews: Record<string, unknown>[];
    primary_model_name: string;
    verifier_model_name: string;
    same_model_verification: boolean;
    prompt_version: string;
    parser_version: string;
    failure_kind?: "provider_error" | "output_invalid" | null;
    warnings: string[];
    automation_allowed: false;
  } | null;
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

export type SocAlertAttentionLevel = "none" | "advisory" | "required";

export type SocDecisionUsability = "usable" | "degraded" | "failed";

export interface SocAlertResult {
  schema_version: "soc.alert_result.v1";
  summary: SocAlertSummary;
  attention_level: SocAlertAttentionLevel;
  attention_reasons: string[];
  decision_usability: SocDecisionUsability;
  requires_human_intervention: boolean;
  queue_item?: SocReviewQueueItem | null;
}

export interface SocAlertResultListResponse {
  items: SocAlertResult[];
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
  | "superseded"
  | "deprecated"
  | "expired";

export type SocMemoryCandidateReviewDecision =
  | "confirm_candidate"
  | "confirm"
  | "reject"
  | "reopen"
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
  thread_id?: string | null;
  message_id?: string | null;
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

export type SocMemoryApplicabilityStatus =
  | "applicable"
  | "partial"
  | "not_applicable"
  | "legacy_anchor_only";

export interface SocMemoryApplicabilitySpec {
  schema_version: "soc.memory_applicability.v1";
  profile_id: string;
  profile_version: string;
  feature_schema_version: string;
  required_facets: Record<string, string[]>;
  optional_facets: Record<string, string[]>;
  excluded_facets: Record<string, string[]>;
  minimum_optional_matches: number;
  minimum_strong_anchor_matches: number;
  context_only_required_facet_keys: string[];
  context_only_missing_facet_keys: string[];
  context_only_similarity_facet_keys: string[];
  policy_version: "soc.memory_applicability_policy.v1";
}

export interface SocMemoryApplicabilityReport {
  schema_version: "soc.memory_applicability_report.v1";
  status: SocMemoryApplicabilityStatus;
  policy_version: string;
  profile_id?: string | null;
  profile_version?: string | null;
  matched_required_facets: Record<string, string[]>;
  missing_required_facet_keys: string[];
  matched_optional_facets: Record<string, string[]>;
  excluded_facet_hits: Record<string, string[]>;
  matched_strong_anchor_count: number;
  context_only_allowed: boolean;
  reason_codes: string[];
}

export interface SocMemoryDecisionDirective {
  schema_version: "soc.memory_decision_directive.v1";
  effect: "reinforce" | "override";
  target_verdict: SocVerdict;
  review_effect: "preserve" | "require" | "clear";
  suggested_action?: string | null;
  minimum_match_score: number;
  required_facet_keys: string[];
  rationale: string;
  policy_version: "soc.memory_decision_directive_policy.v1";
}

export interface SocMemoryBusinessLesson {
  schema_version:
    | "soc.memory_business_lesson.v1"
    | "soc.memory_business_lesson.v2";
  detection_scenario?: string | null;
  observed_event?: string | null;
  conclusion: string;
  business_rationale: string[];
  applicability_conditions: string[];
  generalization_boundaries: string[];
  invalidation_conditions: string[];
  handling_guidance: string[];
}

export interface SocMemoryLessonDraftSource {
  schema_version: "soc.memory_lesson_draft_source.v1";
  source_ref: string;
  source_kind:
    | "candidate"
    | "cohort"
    | "facet"
    | "applicability"
    | "lineage"
    | "reviewer_verdict"
    | "reviewer_context";
  label: string;
  value: string;
}

export interface SocMemoryBusinessLessonDraftProvenance {
  schema_version: "soc.memory_business_lesson_draft_provenance.v1";
  generator_id: string;
  model_name: string;
  prompt_version: string;
  prompt_hash: string;
  response_hash: string;
  repair_applied: boolean;
  repair_actions: string[];
  repair_prompt_hash?: string | null;
  provider_call_count: number;
  output_repair_call_count: number;
  usage: Record<string, string | number>;
  metadata: Record<string, string | number | boolean | null>;
}

export interface SocMemoryBusinessLessonDraftRationale {
  schema_version: "soc.memory_business_lesson_draft_rationale.v1";
  statement: string;
  source_refs: string[];
}

export interface SocMemoryBusinessLessonDraft {
  schema_version: "soc.memory_business_lesson_draft.v1";
  candidate_id: string;
  reviewer_verdict: SocVerdict;
  lesson: SocMemoryBusinessLesson;
  supporting_source_refs: string[];
  rationale_sources: SocMemoryBusinessLessonDraftRationale[];
  source_catalog: SocMemoryLessonDraftSource[];
  uncertainties: string[];
  provenance: SocMemoryBusinessLessonDraftProvenance;
  decision_impact: "none";
  review_required: true;
  persistence_performed: false;
  generated_at: string;
}

export interface SocMemoryBusinessLessonDraftRequest {
  reviewer_verdict: SocVerdict;
  reviewer_context?: string | null;
  promoted_facet_keys?: string[];
}

export type SocMemoryRevisionIssueType =
  | "incorrect_conclusion"
  | "applicability_too_broad"
  | "lesson_incomplete";

export type SocMemoryRevisionOrigin = "observed_use" | "operator_direct";

export interface SocMemoryRevisionLineage {
  schema_version: "soc.memory_revision_lineage.v1";
  predecessor_memory_id: string;
  predecessor_memory_version: number;
  predecessor_content_hash: string;
  predecessor_facets_hash: string;
  suspended_record_version: number;
  revision_origin: SocMemoryRevisionOrigin;
  source_memory_use_id?: string | null;
  source_run_id?: string | null;
  source_alert_id?: string | null;
  issue_type: SocMemoryRevisionIssueType;
  reason: string;
  requested_at: string;
}

export interface SocMemoryRevisionCandidateCreateRequest {
  expected_record_version: number;
  source_run_id?: string | null;
  issue_type: SocMemoryRevisionIssueType;
  reason: string;
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
  applicability?: SocMemoryApplicabilitySpec | null;
  decision_impact: string;
  runtime_decision_allowed: false;
  review_required: true;
  review_owner?: string | null;
  reviewed_by?: SocActorContext | null;
  reviewed_at?: string | null;
  review_reason?: string | null;
  superseded_by_candidate_id?: string | null;
  superseded_at?: string | null;
  supersession_reason?: string | null;
  labels: string[];
  revision_lineage?: SocMemoryRevisionLineage | null;
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
  business_lesson?: SocMemoryBusinessLesson | null;
  reviewed_verdict?: SocVerdict | null;
  facets: Record<string, string[]>;
  evidence_refs: string[];
  validity: SocMemoryCandidateValidity;
  confidence: number;
  decision_impact: string;
  applicability?: SocMemoryApplicabilitySpec | null;
  decision_directive?: SocMemoryDecisionDirective | null;
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
  revision_lineage?: SocMemoryRevisionLineage | null;
  superseded_by_memory_id?: string | null;
  superseded_at?: string | null;
  supersession_reason?: string | null;
  labels: string[];
  metadata: Record<string, unknown>;
}

export interface SocMemoryRevisionCandidateCreateResult {
  schema_version: "soc.memory_revision_candidate_create_result.v1";
  candidate: SocMemoryCandidate;
  predecessor_record: SocMemoryRecord;
  previous_record_version: number;
  previous_retrieval_enabled: boolean;
  audit_id?: string | null;
  created_at: string;
}

export interface SocMemoryRecordListResponse {
  items: SocMemoryRecord[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export type SocMemoryUseEffect =
  | "context_only"
  | "reinforced"
  | "overridden"
  | "conflicted";

export interface SocMemoryUseRecord {
  schema_version: "soc.memory_use.v1";
  use_id: string;
  idempotency_key: string;
  memory_id: string;
  memory_version: number;
  memory_content_hash: string;
  memory_facets_hash: string;
  run_id: string;
  alert_id: string;
  tenant_id?: string | null;
  context_ref: string;
  retrieval_policy_version: string;
  retrieval_score: number;
  matched_facets: Record<string, string[]>;
  applicability_report: SocMemoryApplicabilityReport;
  base_verdict: SocVerdict;
  effective_verdict: SocVerdict;
  effect: SocMemoryUseEffect;
  directive_applied: boolean;
  decision_transition_id?: string | null;
  created_at: string;
}

export interface SocMemoryFeedbackEvent {
  schema_version: "soc.memory_feedback.v1";
  feedback_id: string;
  use_id: string;
  memory_id: string;
  memory_version: number;
  run_id: string;
  alert_id: string;
  source: string;
  trust: string;
  final_verdict: SocVerdict;
  memory_reviewed_verdict?: SocVerdict | null;
  memory_target_verdict?: SocVerdict | null;
  directive_was_active: boolean;
  applicability_status?: string | null;
  alignment: string;
  reason: string;
  source_ref: string;
  actor_id: string;
  created_at: string;
}

export interface SocMemoryHealthRecord {
  schema_version: "soc.memory_health.v1";
  memory_id: string;
  memory_version: number;
  version: number;
  status: "healthy" | "watch" | "suspended";
  use_count: number;
  support_count: number;
  contradiction_count: number;
  not_applicable_count: number;
  unknown_count: number;
  last_use_at?: string | null;
  last_feedback_at?: string | null;
  suspension_reason?: string | null;
  updated_at: string;
}

export interface SocMemoryRevisionProposal {
  schema_version: "soc.memory_revision_proposal.v1";
  proposal_id: string;
  memory_id: string;
  memory_version: number;
  source_feedback_id: string;
  status: "pending_review" | "accepted" | "rejected";
  reason: string;
  proposed_excluded_facets: Record<string, string[]>;
  proposed_target_verdict?: SocVerdict | null;
  created_at: string;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  review_reason?: string | null;
}

export interface SocMemoryLineageReport {
  schema_version: "soc.memory_lineage_report.v1";
  record: SocMemoryRecord;
  uses: SocMemoryUseRecord[];
  feedback: SocMemoryFeedbackEvent[];
  health: SocMemoryHealthRecord[];
  revision_proposals: SocMemoryRevisionProposal[];
}

export interface SocMemoryRecordMatchTestRequest {
  run_id?: string | null;
  alert_id?: string | null;
}

export interface SocMemoryRecordMatchTestResult {
  schema_version: "soc.memory_record_match_test_result.v1";
  record: SocMemoryRecord;
  run_id: string;
  alert_id: string;
  profile_id: string;
  profile_version: string;
  matched: boolean;
  match?: SocMemoryMatch | null;
  exclusion_reasons: string[];
  retrieval: SocMemoryRetrievalResult;
  tested_at: string;
}

export type SocMemoryProfileState = "current" | "legacy" | "unregistered";

export type SocMemoryPatternStageFilter =
  | "collecting"
  | "awaiting_review"
  | "materializing"
  | "persisted"
  | "terminal";

export type SocMemoryFutureUseState =
  | "not_ready"
  | "paused"
  | "reference_only"
  | "exact_match_decision"
  | "blocked";

export type SocMemoryPatternLifecycleState =
  | "collecting"
  | "candidate_pending"
  | "candidate_intermediate"
  | "memory_inactive"
  | "memory_active"
  | "terminal_history";

export interface SocMemoryCenterCandidateRef {
  candidate_id: string;
  status: SocMemoryCandidateStatus;
  summary: string;
  support_count_at_creation: number;
  distinct_source_count_at_creation: number;
  superseded_by_candidate_id?: string | null;
}

export interface SocMemoryCenterRecordRef {
  memory_id: string;
  version: number;
  status: SocMemoryRecordStatus;
  summary: string;
  retrieval_enabled: boolean;
  decision_directive_ready: boolean;
  retrieval_valid_until?: string | null;
  retrieval_review_due_at?: string | null;
}

export interface SocMemoryCenterPatternSummary {
  schema_version: "soc.memory_center_pattern.v1";
  lineage_key: string;
  tenant_id: string;
  environment: string;
  data_class: "simulation" | "operational";
  pattern_dimension: string;
  pattern_value: string;
  pattern_label: string;
  profile_id: string;
  profile_version: string;
  feature_schema_version: string;
  current_profile_version?: string | null;
  current_feature_schema_version?: string | null;
  profile_state: SocMemoryProfileState;
  lifecycle_state: SocMemoryPatternLifecycleState;
  future_use_state: SocMemoryFutureUseState;
  attention_reasons: string[];
  support_count: number;
  distinct_source_count: number;
  aggregation_window_count: number;
  candidate_snapshot_count: number;
  reinforcement_count: number;
  first_observed_at: string;
  last_observed_at: string;
  first_window_start: string;
  last_window_end: string;
  candidate?: SocMemoryCenterCandidateRef | null;
  memory_record?: SocMemoryCenterRecordRef | null;
}

export interface SocMemoryCenterMetrics {
  pattern_count: number;
  aggregation_window_count: number;
  observation_count: number;
  pending_candidate_count: number;
  confirmed_memory_count: number;
  retrieval_enabled_memory_count: number;
  superseded_candidate_count: number;
  legacy_profile_pattern_count: number;
  unregistered_profile_pattern_count: number;
}

export interface SocMemoryCenterOverview {
  schema_version: "soc.memory_center_overview.v1";
  metrics: SocMemoryCenterMetrics;
  items: SocMemoryCenterPatternSummary[];
  terminal_history_count: number;
  total: number;
  limit: number;
  offset: number;
  generated_at: string;
}

export interface SocMemoryPatternObservation {
  schema_version: string;
  observation_id: string;
  aggregation_key: string;
  lineage_key: string;
  tenant_id: string;
  environment: string;
  data_class: "simulation" | "operational";
  profile_id: string;
  profile_version: string;
  feature_schema_version: string;
  source: {
    source_type: string;
    source_id: string;
    transport_ref: string;
    run_id: string;
    alert_id: string;
    observed_at: string;
  };
  signature: {
    dimension: string;
    value: string;
    label: string;
    origin: string;
    facets: Record<string, string[]>;
  };
  lesson?: {
    verdict: SocVerdict;
    risk_class: "risk" | "benign" | "unresolved";
    needs_review: boolean;
    summary: string;
    reason: string;
    recommended_action: string;
  } | null;
  window_start: string;
  window_end: string;
  created_at: string;
}

export interface SocMemoryCenterPatternDetail {
  schema_version: "soc.memory_center_pattern_detail.v1";
  pattern: SocMemoryCenterPatternSummary;
  candidates: SocMemoryCandidate[];
  memory_records: SocMemoryRecord[];
  observations: SocMemoryPatternObservation[];
  observation_total: number;
  observation_limit: number;
  observation_offset: number;
  suggested_successor_candidate_id?: string | null;
}

export interface SocMemoryCandidateSupersessionRequest {
  successor_candidate_id: string;
  reason: string;
}

export interface SocMemoryCandidateSupersessionResult {
  schema_version: "soc.memory_candidate_supersession_result.v1";
  candidate: SocMemoryCandidate;
  successor: SocMemoryCandidate;
  previous_status: SocMemoryCandidateStatus;
  basis: "profile_upgrade_same_source_alert";
  superseded_at: string;
}

export type SocMemoryWorkbenchPhase =
  | "construction"
  | "held_out"
  | "additional";

export interface SocMemoryWorkbenchDecisionStage {
  stage: "base" | "memory" | "tenant_policy" | "effective";
  status: string;
  verdict: SocVerdict;
  confidence: number;
  needs_review: boolean;
  suggested_action: string;
  disposition?: string | null;
  source_id?: string | null;
  summary: string;
}

export interface SocMemoryWorkbenchMemoryContext {
  context_ref: string;
  label: string;
  source_id: string;
  summary: string;
}

export interface SocMemoryWorkbenchAlert {
  alert_id: string;
  phase: SocMemoryWorkbenchPhase;
  phase_order: number;
  observed_at: string;
  endpoint?: string | null;
  host_name?: string | null;
  process_names: string[];
  workflow_state: "locked" | "ready" | "analysis_only" | "completed" | "failed";
  can_process: boolean;
  run_id?: string | null;
  analysis_status?: string | null;
  model_name?: string | null;
  prompt_version?: string | null;
  total_duration_ms?: number | null;
  output_quality?: string | null;
  base_verdict?: SocVerdict | null;
  base_confidence?: number | null;
  base_needs_review?: boolean | null;
  effective_verdict?: SocVerdict | null;
  effective_confidence?: number | null;
  effective_needs_review?: boolean | null;
  analysis_summary?: string | null;
  analysis_reason?: string | null;
  queue_id?: string | null;
  observation_id?: string | null;
  aggregation_key?: string | null;
  pattern_support_count?: number | null;
  pattern_distinct_source_count?: number | null;
  pattern_quality_gate_passed?: boolean | null;
  pattern_consistency_ratio?: number | null;
  memory_contexts: SocMemoryWorkbenchMemoryContext[];
  decision_stages: SocMemoryWorkbenchDecisionStage[];
}

export interface SocMemoryWorkbenchCandidate {
  candidate_id: string;
  status: SocMemoryCandidateStatus;
  candidate_type: SocMemoryCandidateType;
  summary: string;
  support_count: number;
  distinct_source_count: number;
  consistency_ratio: number;
  source_run_id?: string | null;
  source_alert_id?: string | null;
  review_queue_id?: string | null;
  memory_id?: string | null;
  memory_status?: SocMemoryRecordStatus | null;
  retrieval_enabled: boolean;
  decision_directive_ready: boolean;
  business_lesson_ready: boolean;
}

export interface SocMemoryWorkbenchState {
  schema_version: "soc.memory_dev_workbench.v1";
  safety: {
    environment: "dev";
    database_backend: "sqlite";
    database_file: string;
    source_data_class: "operational";
    historical_replay: true;
    internal_providers: "off_or_mock";
    tenant_policy: "disabled" | "deterministic" | "deterministic_and_llm";
    software_path_fast_policy: boolean;
    external_action_execution: false;
  };
  source: {
    file_name: string;
    sha256: string;
    selected_alert_count: 14;
  };
  model: {
    mode: string;
    model_name?: string | null;
    thinking_enabled: boolean;
    role_verifier_enabled: boolean;
    role_verifier_model_name?: string | null;
  };
  cohort: {
    tenant_id: "pingan";
    rule_code: "RPAADM_002010";
    rule_name: "GalaxyLab_T1003-SAM-Dumping";
    detection_key: "leagsoft-edr:rule_code:rpaadm_002010";
    behavior_fingerprint: string;
    behavior_components: string[];
    construction_target: 5;
    held_out_target: 1;
    additional_count: 8;
  };
  progress: {
    processed_count: number;
    construction_processed: number;
    construction_target: 5;
    candidate_state:
      | "collecting"
      | "quality_gate_blocked"
      | "pending_review"
      | "confirmed_candidate"
      | "confirmed"
      | "rejected"
      | "superseded"
      | "expired"
      | "deprecated";
    memory_state:
      | "not_created"
      | "confirmed_inactive"
      | "confirmed_context_only"
      | "decision_ready";
    held_out_unlocked: boolean;
    held_out_processed: boolean;
    next_alert_id?: string | null;
    next_action:
      | "process_construction"
      | "review_candidate"
      | "enable_memory"
      | "process_held_out"
      | "process_additional"
      | "quality_gate_blocked"
      | "complete";
  };
  candidate?: SocMemoryWorkbenchCandidate | null;
  alerts: SocMemoryWorkbenchAlert[];
}

export interface SocMemoryWorkbenchProcessResult {
  schema_version: "soc.memory_dev_workbench_process.v1";
  alert_id: string;
  run_id?: string | null;
  observation_id?: string | null;
  idempotent: boolean;
  state: SocMemoryWorkbenchState;
}

export type SocCorpusWorkbenchReadiness =
  | "candidate_window"
  | "recurrent_strong"
  | "singleton_strong"
  | "recurrent_context_only"
  | "context_only_singleton"
  | "fingerprint_missing";

export type SocCorpusOperationalLabel = "忽略" | "转交";
export type SocCorpusProjectedDisposition =
  | "ignore"
  | "transfer"
  | "undetermined";
export type SocCorpusComparisonStatus =
  | "matched"
  | "mismatched"
  | "unscored"
  | "not_run"
  | "unlabeled";
export type SocCorpusLabelTemporalStatus =
  | "valid"
  | "label_time_missing"
  | "label_precedes_alert"
  | "unlabeled";

export interface SocCorpusWorkbenchMemoryContext {
  context_ref: string;
  label: string;
  source_id: string;
  summary: string;
}

export interface SocCorpusWorkbenchDecisionStage {
  stage: "base" | "memory" | "tenant_policy" | "effective";
  status: string;
  verdict: SocVerdict;
  confidence: number;
  needs_review: boolean;
  suggested_action: string;
  disposition?: string | null;
  source_id?: string | null;
  summary: string;
}

export type SocCorpusExecutionStatus =
  | "not_started"
  | "running"
  | "analysis_complete"
  | "completed"
  | "failed";

export type SocCorpusExecutionPhaseStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "skipped";

export interface SocCorpusWorkbenchExecutionStep {
  step_name: string;
  label: string;
  status: SocCorpusExecutionPhaseStatus;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  warning_count: number;
  error?: string | null;
}

export interface SocCorpusWorkbenchExecutionPhase {
  phase: string;
  label: string;
  status: SocCorpusExecutionPhaseStatus;
  summary: string;
  duration_ms?: number | null;
  metrics: Record<string, string | number | boolean>;
  steps: SocCorpusWorkbenchExecutionStep[];
}

export interface SocCorpusWorkbenchExecution {
  schema_version: "soc.corpus_dev_execution.v1";
  alert_id: string;
  status: SocCorpusExecutionStatus;
  current_phase?: string | null;
  run_id?: string | null;
  run_status?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  elapsed_ms?: number | null;
  total_duration_ms?: number | null;
  model_name?: string | null;
  provider_purpose?: string | null;
  provider_attempt_count: number;
  observation_id?: string | null;
  aggregation_key?: string | null;
  candidate_id?: string | null;
  phases: SocCorpusWorkbenchExecutionPhase[];
}

export type SocCorpusWorkbenchAuditArtifactStatus =
  | "available"
  | "partial"
  | "unavailable";

export interface SocCorpusWorkbenchAuditArtifact {
  sequence: number;
  artifact_id: string;
  file_name: string;
  phase: string;
  title: string;
  description: string;
  status: SocCorpusWorkbenchAuditArtifactStatus;
  source: "persisted_run" | "persisted_downstream" | "read_model_projection";
  metrics: Record<string, string | number | boolean>;
  review_guide: string[];
  payload: Record<string, unknown>;
}

export interface SocCorpusWorkbenchAuditBundle {
  schema_version: "soc.corpus_dev_audit_bundle.v1";
  alert_id: string;
  run_id: string;
  generated_at: string;
  pipeline_version: string;
  model_name: string;
  prompt_version: string;
  input_hash?: string | null;
  safety: {
    dev_only: true;
    admin_only: true;
    contains_raw_alert_data: true;
    contains_model_context: true;
    reexecutes_runtime: false;
    mutates_state: false;
  };
  execution: SocCorpusWorkbenchExecution;
  artifacts: SocCorpusWorkbenchAuditArtifact[];
}

export interface SocCorpusWorkbenchAlert {
  alert_id: string;
  source_index: number;
  sequence_number: number;
  observed_at: string;
  topic?: string | null;
  source_type: string;
  source_system?: string | null;
  product?: string | null;
  detection_key?: string | null;
  rule_code?: string | null;
  rule_name?: string | null;
  category?: string | null;
  severity?: string | null;
  endpoint?: string | null;
  host_name?: string | null;
  process_names: string[];
  behavior_fingerprint?: string | null;
  behavior_components: string[];
  behavior_strength?: string | null;
  decision_eligible: boolean;
  readiness: SocCorpusWorkbenchReadiness;
  group_id: string;
  group_alert_count: number;
  window_alert_count: number;
  window_start: string;
  window_end: string;
  workflow_state:
    | "ready"
    | "running"
    | "analysis_only"
    | "completed"
    | "failed";
  can_process: boolean;
  blocked_by_alert_id?: string | null;
  run_id?: string | null;
  replay_of_run_id?: string | null;
  analysis_status?: string | null;
  model_name?: string | null;
  prompt_version?: string | null;
  total_duration_ms?: number | null;
  output_quality?: string | null;
  failure_kind?: string | null;
  failure_message?: string | null;
  base_verdict?: SocVerdict | null;
  base_confidence?: number | null;
  base_needs_review?: boolean | null;
  effective_verdict?: SocVerdict | null;
  effective_confidence?: number | null;
  effective_needs_review?: boolean | null;
  analysis_summary?: string | null;
  analysis_reason?: string | null;
  queue_id?: string | null;
  observation_id?: string | null;
  aggregation_key?: string | null;
  pattern_support_count?: number | null;
  pattern_distinct_source_count?: number | null;
  pattern_quality_gate_passed?: boolean | null;
  pattern_consistency_ratio?: number | null;
  candidate_id?: string | null;
  candidate_status?: string | null;
  manual_candidate_id?: string | null;
  manual_candidate_status?: string | null;
  memory_id?: string | null;
  memory_status?: string | null;
  memory_contexts: SocCorpusWorkbenchMemoryContext[];
  memory_directive_applied: boolean;
  memory_effect?: string | null;
  decision_stages: SocCorpusWorkbenchDecisionStage[];
  operational_label_available: boolean;
  operational_label_revealed: boolean;
  operational_label?: SocCorpusOperationalLabel | null;
  operational_label_observed_at?: string | null;
  operational_label_method?: string | null;
  operational_label_reason?: string | null;
  operational_label_status?: string | null;
  label_temporal_status: SocCorpusLabelTemporalStatus;
  base_operational_projection: SocCorpusProjectedDisposition;
  effective_operational_projection: SocCorpusProjectedDisposition;
  base_label_comparison: SocCorpusComparisonStatus;
  effective_label_comparison: SocCorpusComparisonStatus;
  base_projection_basis?: string | null;
  effective_projection_basis?: string | null;
}

export interface SocCorpusWorkbenchGroup {
  group_id: string;
  source_type: string;
  detection_key?: string | null;
  rule_code?: string | null;
  rule_name?: string | null;
  behavior_fingerprint?: string | null;
  behavior_components: string[];
  decision_eligible: boolean;
  alert_count: number;
  window_count: number;
  max_window_alert_count: number;
  candidate_window_count: number;
  processed_count: number;
  memory_hit_count: number;
}

export type SocLeadershipDemoAvailability = "ready" | "drifted" | "unavailable";

export interface SocLeadershipDemoTarget {
  target_id: string;
  label: string;
  source_type: string;
  expected_group_id: string;
  actual_group_id?: string | null;
  primary_alert_id: string;
  rehearsal_alert_ids: string[];
  availability: SocLeadershipDemoAvailability;
  missing_alert_ids: string[];
  drifted_alert_ids: string[];
}

export interface SocLeadershipDemoChapter {
  chapter_id: string;
  sequence: number;
  tier: "primary" | "backup";
  expected_memory_use: "context_only" | "exact_match";
  title: string;
  objective: string;
  presenter_note: string;
  capabilities: string[];
  operator_steps: string[];
  success_cues: string[];
  targets: SocLeadershipDemoTarget[];
}

export interface SocLeadershipDemoGuide {
  schema_version: "soc.leadership_demo_guide.v2";
  guide_version: string;
  title: string;
  purpose: string;
  ready: boolean;
  primary_chapter_count: number;
  backup_chapter_count: number;
  chapters: SocLeadershipDemoChapter[];
}

export interface SocCorpusWorkbenchState {
  schema_version: "soc.corpus_dev_workbench.v3";
  safety: {
    environment: "dev";
    database_backend: "sqlite";
    database_file: string;
    source_data_class: "operational";
    historical_replay: true;
    internal_providers: "off_or_mock";
    tenant_policy: "disabled" | "deterministic" | "deterministic_and_llm";
    software_path_fast_policy: boolean;
    external_action_execution: false;
    memory_scope: string;
    pattern_window_days: number;
    execution_mode: "interactive_exploration";
    chronology_enforced: false;
    rerun_enabled: true;
    causal_evaluation_allowed: false;
    replay_order: "operator_selected";
    label_visibility: "hidden_until_runtime_decision";
  };
  source: {
    file_name: string;
    sha256: string;
    alert_count: number;
    labeled_alert_count: number;
    unlabeled_alert_count: number;
    first_event_time: string;
    last_event_time: string;
    sort_order: "canonical_event_time_asc_alert_id_asc";
    index_file_name?: string | null;
    index_sha256?: string | null;
    payload_store_file_name?: string | null;
    payload_store_sha256?: string | null;
  };
  model: {
    mode: string;
    model_name?: string | null;
    thinking_enabled: boolean;
    role_verifier_enabled: boolean;
    role_verifier_model_name?: string | null;
  };
  readiness: {
    total_alert_count: number;
    fingerprint_coverage_count: number;
    decision_eligible_alert_count: number;
    recurrent_group_count: number;
    recurrent_alert_count: number;
    candidate_window_group_count: number;
    candidate_window_alert_count: number;
    processed_count: number;
    failed_count: number;
    memory_hit_alert_count: number;
  };
  evaluation: {
    label_kind: "operational_disposition";
    label_counts: Record<string, number>;
    temporally_valid_label_count: number;
    temporally_invalid_label_count: number;
    unlabeled_count: number;
    processed_labeled_count: number;
    base_matched_count: number;
    base_mismatched_count: number;
    base_unscored_count: number;
    base_match_rate?: number | null;
    effective_matched_count: number;
    effective_mismatched_count: number;
    effective_unscored_count: number;
    effective_match_rate?: number | null;
  };
  leadership_demo: SocLeadershipDemoGuide;
  groups: SocCorpusWorkbenchGroup[];
  alerts: SocCorpusWorkbenchAlert[];
}

export interface SocCorpusWorkbenchProcessResult {
  schema_version: "soc.corpus_dev_workbench_process.v3";
  alert_id: string;
  run_id?: string | null;
  replay_of_run_id?: string | null;
  observation_id?: string | null;
  execution_mode: "initial" | "rerun" | "pattern_resume";
  pattern_observation_reused: boolean;
  idempotent: boolean;
  state: SocCorpusWorkbenchState;
}

export interface SocMemoryRunPromotionRequest {
  note?: string;
  confidence?: number;
}

export interface SocMemoryRunPromotionResult {
  schema_version: "soc.memory_run_promotion_result.v1";
  run_id: string;
  alert_id: string;
  memory_candidate?: SocMemoryCandidate | null;
  memory_admission: SocMemoryAdmissionDecision;
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
  policy_version?:
    | "soc.memory_retrieval_policy.v1"
    | "soc.memory_retrieval_policy.v2";
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
  anchor_match_reasons: string[];
  matched_anchor_facets: Record<string, string[]>;
  applicability_report?: SocMemoryApplicabilityReport | null;
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
  skipped_missing_strong_anchor: number;
  skipped_not_applicable: number;
  skipped_below_min_score: number;
  returned_count: number;
  returned_context_only_count: number;
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
  | "investigation_addendum"
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

export interface SocInvestigationAddendumItem {
  plan_action_id: string;
  route: string;
  action: string;
  adapter_id?: string | null;
  status: string;
  attempt_count: number;
  retry_count: number;
  provider_invoked: boolean;
  result_mode?: "mock" | "real" | null;
  evidence_id?: string | null;
  evidence_available: boolean;
  evidence_summary?: string | null;
  latest_attempt_latency_ms?: number | null;
}

export interface SocInvestigationAddendum {
  schema_version: string;
  addendum_id: string;
  projection_version: string;
  source_report_id: string;
  source_hash: string;
  execution_id: string;
  run_id: string;
  alert_id: string;
  trigger: string;
  execution_status: string;
  generated_at: string;
  source_updated_at: string;
  base_runtime_status: string;
  base_runtime_verdict?: SocVerdict | null;
  summary: string;
  items: SocInvestigationAddendumItem[];
  evidence_refs: string[];
  evidence_coverage_ratio: number;
  analyst_attention_required: boolean;
  measurement_gaps: string[];
  addendum_kind: "read_only_execution_summary";
  reasoning_status: "not_requested";
  new_conclusion_produced: false;
  grounding_status: "deterministic_evidence_reference_check";
  projection_persisted: false;
  durable_sources_persisted: true;
  shadow_only: true;
  decision_impact: "none";
  base_run_mutated: false;
  automation_allowed: false;
  auto_close_allowed: false;
  confirmed_memory_write_allowed: false;
  high_risk_actions_allowed: false;
}

export interface SocUnifiedInvestigationView {
  schema_version: string;
  view_id: string;
  queue_id?: string | null;
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
  investigation_addenda: SocInvestigationAddendum[];
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
  record_lesson?: SocMemoryBusinessLesson | null;
  record_applicability?: SocMemoryApplicabilitySpec | null;
  decision_directive?: SocMemoryDecisionDirective | null;
  confirmed_verdict?: SocVerdict | null;
  apply_to_future_matches?: boolean;
  clear_review_on_match?: boolean;
  activate_retrieval?: boolean;
  activation_valid_until?: string | null;
  activation_review_after_days?: number | null;
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
  investigation_addenda: SocInvestigationAddendum[];
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

export interface SocAlertInvestigationContext {
  schema_version: "soc.alert_investigation_context.v1";
  result: SocAlertResult;
  run: SocAnalysisRun;
  audit_records: SocDecisionAuditRecord[];
  similar_alerts: SocSimilarAlertMatch[];
  action_evidence: SocInvestigationEvidence[];
  investigation_addenda: SocInvestigationAddendum[];
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

export interface SocLeadAgentConclusionAcceptanceRequest {
  message_id: string;
  acceptance_reason: string;
}

export interface SocReviewNoteResult {
  queue_item: SocReviewQueueItem;
  memory_candidate?: SocMemoryCandidate | null;
  memory_admission?: SocMemoryAdmissionDecision | null;
}

export interface SocMemoryAdmissionDecision {
  schema_version: "soc.memory_admission_decision.v1";
  policy_version: "soc.memory_admission_policy.v1";
  status: "admitted" | "observed_only";
  source_type: string;
  candidate_type: SocMemoryCandidateType;
  quality_score: number;
  reason_codes: string[];
  reusable_facets: Record<string, string[]>;
  command_hash: string;
  candidate_id?: string | null;
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

export type SocOperationsAvailability =
  | "available"
  | "unavailable"
  | "not_configured"
  | "not_measured";

export interface SocPersistedOperationsMetrics {
  measurement_scope: "lifetime";
  analysis_run_count: number;
  analysis_run_status_counts: Record<string, number>;
  latest_analysis_started_at?: string | null;
  latest_analysis_completed_at?: string | null;
  open_review_count: number;
  oldest_open_review_created_at?: string | null;
  pending_approval_request_count: number;
  oldest_pending_approval_created_at?: string | null;
  open_normalization_issue_count: number;
  critical_open_normalization_issue_count: number;
  active_normalization_baseline_count: number;
  pending_memory_candidate_count: number;
}

export interface SocOperationsPersistedSnapshot {
  availability: SocOperationsAvailability;
  backend?: string | null;
  metrics?: SocPersistedOperationsMetrics | null;
  error_code?: string | null;
}

export interface SocOperationsKafkaSnapshot {
  availability: SocOperationsAvailability;
  enabled: boolean;
  settings_valid: boolean;
  checked: boolean;
  reachable?: boolean | null;
  bootstrap_server_count: number;
  alert_topic_count: number;
  approval_request_topic_count: number;
  dead_letter_configured: boolean;
  consumer_lag_availability: "not_measured";
  error_code?: string | null;
}

export interface SocOperationsMeasurementGap {
  metric: string;
  availability: "not_measured";
  reason: string;
}

export interface SocOperationsSnapshot {
  schema_version: "soc.operations_snapshot.v1";
  generated_at: string;
  persisted: SocOperationsPersistedSnapshot;
  kafka: SocOperationsKafkaSnapshot;
  measurement_gaps: SocOperationsMeasurementGap[];
  production_slo_evidence_available: false;
}

export type SocRuleRecommendationKind =
  | "insufficient_labels"
  | "upstream_rule_tuning"
  | "rule_split"
  | "fast_path_candidate"
  | "keep_full_analysis"
  | "improve_adapter_or_enrichment"
  | "detection_gap"
  | "monitor";

export type SocRuleRecommendationPriority = "info" | "low" | "medium" | "high";

export interface SocEffectivenessScope {
  schema_version: "soc.effectiveness_scope.v1";
  window_start: string;
  window_end: string;
  tenant_id?: string | null;
  source_type?: string | null;
}

export interface SocRateMetric {
  metric_id: string;
  availability: SocOperationsAvailability;
  numerator: number;
  denominator: number;
  value?: number | null;
  formula: string;
  interpretation: string;
}

export interface SocEffectivenessCoverage {
  total_alert_count: number;
  completed_alert_count: number;
  superseded_run_count: number;
  labeled_alert_count: number;
  high_trust_labeled_alert_count: number;
  label_coverage: SocRateMetric;
  high_trust_label_coverage: SocRateMetric;
}

export interface SocEffectivenessSummary {
  triage_accuracy: SocRateMetric;
  detection_miss_rate: SocRateMetric;
  operational_miss_rate: SocRateMetric;
  transfer_precision: SocRateMetric;
  attack_transfer_recall: SocRateMetric;
  auto_ignore_rate: SocRateMetric;
  wrong_auto_ignore_rate: SocRateMetric;
  human_touch_rate: SocRateMetric;
}

export interface SocComputeEffectiveness {
  run_count: number;
  provider_run_count: number;
  provider_call_count: number;
  token_measured_run_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  average_tokens_per_measured_run?: number | null;
  duration_measured_run_count: number;
  average_total_duration_ms?: number | null;
  repair_run_count: number;
  fallback_run_count: number;
  degraded_run_count: number;
  token_measurement_coverage: SocRateMetric;
  repair_rate: SocRateMetric;
  fallback_rate: SocRateMetric;
  degraded_rate: SocRateMetric;
}

export interface SocRuleImprovementRecommendation {
  schema_version: "soc.rule_improvement_recommendation.v1";
  kind: SocRuleRecommendationKind;
  priority: SocRuleRecommendationPriority;
  title: string;
  rationale: string[];
  suggested_next_step: string;
  reason_codes: string[];
  policy_version: string;
  authority: "advisory";
  status: "candidate";
}

export interface SocRuleEffectiveness {
  schema_version: "soc.rule_effectiveness.v1";
  group_key: string;
  tenant_id?: string | null;
  source_type: string;
  source_system?: string | null;
  detection_identity: string;
  detection_key?: string | null;
  rule_code?: string | null;
  rule_name?: string | null;
  alert_count: number;
  completed_count: number;
  labeled_count: number;
  high_trust_labeled_count: number;
  label_coverage: number;
  final_risk_count: number;
  final_false_positive_count: number;
  confirmed_risk_rate?: number | null;
  false_positive_rate?: number | null;
  triage_accuracy?: number | null;
  miss_rate?: number | null;
  transfer_precision?: number | null;
  auto_ignore_rate: number;
  wrong_auto_ignore_count: number;
  provider_call_count: number;
  provider_run_count: number;
  total_tokens: number;
  average_total_duration_ms?: number | null;
  repair_run_count: number;
  fallback_run_count: number;
  degraded_run_count: number;
  memory_context_use_count: number;
  memory_directive_use_count: number;
  memory_contradiction_count: number;
  recommendation: SocRuleImprovementRecommendation;
}

export interface SocBehaviorGroupEffectiveness {
  schema_version: "soc.behavior_group_effectiveness.v1";
  lineage_key: string;
  behavior_label: string;
  environment: string;
  data_class: string;
  sample_count: number;
  distinct_alert_count: number;
  window_count: number;
  verdict_counts: Record<string, number>;
  first_observed_at: string;
  last_observed_at: string;
  candidate_id?: string | null;
  candidate_status?: string | null;
  memory_id?: string | null;
  memory_version?: number | null;
  memory_status?: string | null;
  retrieval_enabled: boolean;
}

export interface SocMemoryEffectiveness {
  schema_version: "soc.memory_effectiveness.v1";
  memory_id: string;
  memory_version: number;
  summary?: string | null;
  record_status?: string | null;
  retrieval_enabled: boolean;
  use_alert_count: number;
  context_only_count: number;
  directive_count: number;
  high_trust_feedback_count: number;
  support_count: number;
  contradiction_count: number;
  not_applicable_count: number;
  helpful_correction_count: number;
  harmful_override_count: number;
  wrong_auto_ignore_count: number;
  final_outcome_coverage: SocRateMetric;
  directive_accuracy: SocRateMetric;
  source_rule_codes: string[];
  actual_rule_codes: string[];
  last_use_at?: string | null;
  last_feedback_at?: string | null;
  causal_note: "directive_effects_attributable_context_effects_non_causal";
}

export interface SocRuleEffectivenessDetail {
  schema_version: "soc.rule_effectiveness_detail.v1";
  generated_at: string;
  scope: SocEffectivenessScope;
  rule: SocRuleEffectiveness;
  behavior_groups: SocBehaviorGroupEffectiveness[];
  memories: SocMemoryEffectiveness[];
  relationship_note: "memory_rule_relationship_derived_from_actual_runs";
}

export interface SocEffectivenessSnapshot {
  schema_version: "soc.effectiveness_snapshot.v1";
  generated_at: string;
  availability: SocOperationsAvailability;
  scope: SocEffectivenessScope;
  coverage?: SocEffectivenessCoverage | null;
  summary?: SocEffectivenessSummary | null;
  compute?: SocComputeEffectiveness | null;
  rules: SocRuleEffectiveness[];
  recommendation_policy_version: string;
  aggregation_mode: "latest_run_per_alert_sql_v1";
  error_code?: string | null;
  measurement_notes: string[];
}
