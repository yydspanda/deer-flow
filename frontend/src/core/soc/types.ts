export type SocReviewQueueStatus = "open" | "closed";

export type SocReviewQueuePriority = "low" | "medium" | "high";

export type SocVerdict =
  | "true_positive"
  | "suspicious"
  | "false_positive"
  | "unknown"
  | "needs_review";

export type SocEntrySurface = "api" | "web";

export interface SocRequestContext {
  actorId?: string;
  surface?: SocEntrySurface;
  traceId?: string;
  idempotencyKey?: string;
}

export interface SocActorContext {
  actor_id: string;
  actor_type?: string;
  surface: string;
  roles?: string[];
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

export type SocExternalDispositionCanonicalStatus =
  | "closed_true_positive"
  | "closed_false_positive"
  | "closed_benign_true_positive"
  | "suppressed"
  | "escalated"
  | "ignored"
  | "duplicate"
  | "unknown";

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
  external_dispositions: SocExternalDispositionRecord[];
  memory_candidates: SocMemoryCandidate[];
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

export interface SocAgentApprovalRequest {
  schema_version?: "soc.agent_approval_request.v1";
  approval_request_id?: string;
  permission_decision_id: string;
  route: string;
  action: string;
  risk_level: SocAgentRiskLevel;
  reason: string;
  requested_by: SocActorContext;
  source_proposal_id?: string | null;
  action_payload?: Record<string, unknown>;
  context_refs?: Record<string, unknown>;
  status?: "pending";
  created_at?: string;
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
  approval_request: SocAgentApprovalRequest;
  reason: string;
  expires_in_seconds?: number;
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
