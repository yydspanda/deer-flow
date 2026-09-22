import type { SocAnalysisExecutionOptions, SocCorpusBatch } from "./types";

export interface SocCorpusExperiment {
  experiment_id: string;
  name: string;
  plan_id: string;
  member_count: number;
  created_at: string;
}

export interface SocCorpusRoundSelection {
  batch: SocCorpusBatch;
  scope: "reuse" | "explore" | "all";
  group_ids: string[];
  rule_codes: string[];
  alert_ids: string[];
}

export interface SocCorpusRoundCommand {
  experiment_id: string;
  selection: SocCorpusRoundSelection;
  purpose: "memory" | "full_flow";
  options: SocAnalysisExecutionOptions;
  memory_mode: "snapshot" | "none";
  execution_limit: number;
  concurrency: number;
  parent_round_id?: string | null;
}

export type SocCorpusRoundState =
  | "prepared"
  | "running"
  | "paused"
  | "blocked"
  | "completed";

export interface SocCorpusRoundBrief {
  round_id: string;
  batch: SocCorpusBatch;
  state: SocCorpusRoundState;
  created_at: string;
}

export interface SocCorpusRound extends SocCorpusRoundCommand {
  round_id: string;
  state: SocCorpusRoundState;
  state_reason?: string | null;
  version: number;
  created_at: string;
  config_hash: string;
  memory_snapshot: { memory_id: string; version: number }[];
}

export interface SocCorpusRoundProgress {
  round: SocCorpusRound;
  selected_count: number;
  admitted_count: number;
  completed_count: number;
  failed_count: number;
  active_count: number;
  counts: Record<string, number>;
  timing?: {
    elapsed_ms: number | null;
    running_ms: number | null;
    paused_ms: number | null;
    terminal_count: number;
    processed_per_minute: number | null;
    estimated_remaining_seconds: number | null;
    estimate_status:
      | "estimated"
      | "insufficient_samples"
      | "not_running"
      | "finished"
      | "unavailable_history";
  } | null;
}

export function corpusDuration(
  milliseconds: number | null | undefined,
): string {
  if (
    milliseconds == null ||
    !Number.isFinite(milliseconds) ||
    milliseconds < 0
  )
    return "未记录";
  const seconds = Math.ceil(milliseconds / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

export interface SocCorpusExperimentConfiguration {
  defaults: SocAnalysisExecutionOptions;
  can_configure?: boolean;
  saved_options?: SocAnalysisExecutionOptions;
  full_flow_defaults: SocAnalysisExecutionOptions;
  max_concurrency: number;
  concurrency_limit?: number;
  dispatcher_running: boolean;
}

export interface SocCorpusConcurrency {
  max_concurrency: number;
  concurrency_limit: number;
}

export interface SocCorpusRoundResult {
  alert_id: string;
  run_id: string | null;
  group_id: string;
  status: string;
  candidate_id: string | null;
  error_message: string | null;
  snapshot_changed_during_run: boolean;
  summary: {
    recommended_handling?: string | null;
    processing_path?: string | null;
    effective_verdict?: string | null;
    total_duration_ms?: number | null;
    memory_uses?: unknown[];
  };
  label: { scorable?: boolean; expected_handling?: string | null };
}

export interface SocCorpusRoundResults {
  round_id: string;
  total: number;
  items: SocCorpusRoundResult[];
}

export interface SocCorpusComparisonSide {
  run_id: string | null;
  status: string;
  error_message?: string | null;
  snapshot_changed_during_run: boolean;
  summary: Omit<SocCorpusRoundResult["summary"], "memory_uses"> & {
    memory_uses: {
      memory_id: string;
      memory_version?: number;
      directive_applied?: boolean;
      effect?: string;
    }[];
    measurements: {
      total_tokens: number | null;
      provider_call_count: number | null;
      usage_measurement_status?: string | null;
    };
  };
}

export interface SocCorpusRoundComparison {
  round_id: string;
  parent_round_id: string;
  baseline_captured_at: string | null;
  config_changed: boolean | null;
  total: number;
  items: {
    alert_id: string;
    before: SocCorpusComparisonSide | null;
    after: SocCorpusComparisonSide;
    comparison_status:
      | "handling_changed"
      | "handling_unchanged"
      | "not_comparable"
      | "new_sample"
      | "not_captured";
    changed_fields: string[];
  }[];
}

export const corpusRoundStateLabel: Record<SocCorpusRoundState, string> = {
  prepared: "尚未启动",
  running: "运行中",
  paused: "已暂停",
  blocked: "配置或经验已变更",
  completed: "本次额度已完成",
};

export function corpusRoundSelection(
  batch: SocCorpusBatch,
  tier: "main" | "supplementary" | "all",
  groupId: string,
  alertId = "",
): SocCorpusRoundSelection {
  return {
    batch,
    scope:
      batch === "learning" || tier === "main"
        ? "reuse"
        : tier === "supplementary"
          ? "explore"
          : "all",
    group_ids: groupId && groupId !== "all" ? [groupId] : [],
    rule_codes: [],
    alert_ids: alertId.trim() ? [alertId.trim()] : [],
  };
}

export interface SocCorpusQuickState {
  experiment_id: string;
  total: number;
  completed: number;
  active: number;
  remaining: number;
  failed: number;
  pending_candidates: number;
  manual_pending?: boolean;
  running: boolean;
  blocked_reason: string | null;
  items: (SocCorpusRoundResult & {
    job_id: string | null;
    manual_dispatch?: boolean;
    blocked_reason?: string | null;
  })[];
}
