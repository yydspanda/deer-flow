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

export interface SocInvestigationContext {
  schema_version: string;
  queue_item: SocReviewQueueItem;
  run: SocAnalysisRun;
  summary?: SocAlertSummary | null;
  audit_records: SocDecisionAuditRecord[];
  similar_alerts: SocSimilarAlertMatch[];
}

export interface SocReviewCloseRequest {
  reason: string;
}

export interface SocReviewCorrectionRequest {
  verdict: SocVerdict;
  reason: string;
  confidence?: number | null;
}
