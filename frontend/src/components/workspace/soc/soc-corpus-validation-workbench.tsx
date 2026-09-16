"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Clock3Icon,
  DatabaseIcon,
  ExternalLinkIcon,
  EyeIcon,
  FileSearchIcon,
  FilterIcon,
  LoaderCircleIcon,
  ListFilterIcon,
  PlayIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchIcon,
  ShieldCheckIcon,
  XCircleIcon,
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { SocCaseOutcomePanel } from "@/components/workspace/soc/soc-case-outcome-panel";
import { SocCorpusGroupPicker } from "@/components/workspace/soc/soc-corpus-group-picker";
import {
  availableCorpusRunSettings,
  readCorpusRunSettings,
  RUN_SETTINGS_STORAGE_KEY,
  SocCorpusRunSettings,
  SocRunOptionsSummary,
} from "@/components/workspace/soc/soc-corpus-run-settings";
import { SocDecisionLineageTable } from "@/components/workspace/soc/soc-decision-lineage-table";
import { formatSocDevPolicyLabel } from "@/components/workspace/soc/soc-dev-policy-label";
import { SocHandlingBadge } from "@/components/workspace/soc/soc-handling-badge";
import { SocLeadershipDemoGuidePanel } from "@/components/workspace/soc/soc-leadership-demo-guide";
import { memoryRunUsageCopy } from "@/components/workspace/soc/soc-memory-copy";
import { SocMemoryLearningStatus } from "@/components/workspace/soc/soc-memory-learning-status";
import { SocRunMemoryContext } from "@/components/workspace/soc/soc-run-memory-context";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  formatCorpusGroupOption,
  SocApiError,
  useProcessSocCorpusWorkbenchAlert,
  usePromoteSocRunToMemory,
  useSocCorpusWorkbench,
  useSocCorpusWorkbenchActivity,
  useSocCorpusWorkbenchExecution,
} from "@/core/soc";
import type {
  SocAnalysisExecutionOptions,
  SocCorpusComparisonStatus,
  SocCorpusWorkbenchAlert,
  SocCorpusWorkbenchExecution,
  SocCorpusWorkbenchExecutionPhase,
  SocCorpusWorkbenchReadiness,
  SocCorpusWorkbenchState,
  SocLeadershipDemoTarget,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;
const FILTER_STORAGE_KEY = "soc.corpus-validation.filters.v1";

const SocCorpusAuditViewer = dynamic(
  () =>
    import("@/components/workspace/soc/soc-corpus-audit-viewer").then(
      (module) => module.SocCorpusAuditViewer,
    ),
  {
    loading: () => <Skeleton className="h-96 w-full rounded-none" />,
  },
);

const READINESS: Record<
  SocCorpusWorkbenchReadiness,
  { label: string; className: string; title: string; rank: number }
> = {
  candidate_window: {
    label: "可沉淀经验",
    className: "border-emerald-300 bg-emerald-50 text-emerald-800",
    title: "强指纹且同一固定聚合窗口至少 5 条；仍需运行结果通过一致性质量门",
    rank: 0,
  },
  recurrent_strong: {
    label: "同类行为重复",
    className: "border-sky-300 bg-sky-50 text-sky-800",
    title: "长期同类告警至少 2 条，但当前固定聚合窗口不足 5 条",
    rank: 1,
  },
  singleton_strong: {
    label: "可识别单例",
    className: "border-zinc-300 bg-zinc-50 text-zinc-700",
    title: "具备决策级行为指纹，但当前语料没有同类重复样本",
    rank: 2,
  },
  recurrent_context_only: {
    label: "弱特征重复",
    className: "border-amber-300 bg-amber-50 text-amber-800",
    title: "存在重复行为，但特征不足以直接复用历史结论，只能辅助模型研判",
    rank: 3,
  },
  context_only_singleton: {
    label: "弱特征单例",
    className: "border-zinc-300 bg-zinc-50 text-zinc-600",
    title: "只有弱行为信号且没有同类重复样本",
    rank: 4,
  },
  fingerprint_missing: {
    label: "无法稳定归类",
    className: "border-red-300 bg-red-50 text-red-800",
    title: "可运行 Runtime，但不适合作为当前 Memory 泛化验证样本",
    rank: 5,
  },
};

type ReadinessFilter = SocCorpusWorkbenchReadiness | "all";
type ComparisonFilter = SocCorpusComparisonStatus | "all" | "labeled";

interface CorpusFilterSnapshot {
  search: string;
  readiness: ReadinessFilter;
  comparison: ComparisonFilter;
  sourceType: string;
  groupId: string;
  unprocessedOnly: boolean;
}

interface RunFeedback {
  alertId: string;
  status: "running" | "completed" | "failed";
  message: string;
  baselineRunId?: string | null;
  baselineWorkflowState?: SocCorpusWorkbenchAlert["workflow_state"];
}

function completedRunFeedback(alert: SocCorpusWorkbenchAlert): RunFeedback {
  const replayPrefix = alert.replay_of_run_id
    ? "本次已创建新的 Runtime Run；"
    : "";
  return {
    alertId: alert.alert_id,
    status: "completed",
    message: replayPrefix
      ? `${replayPrefix}未重复累计同一告警的模式样本。`
      : "完整研判链路已完成，可按需查看详细结果。",
  };
}

function workflowStateLabel(state: SocCorpusWorkbenchAlert["workflow_state"]) {
  return {
    ready: "待运行",
    running: "运行中",
    analysis_only: "待写入模式",
    completed: "已完成",
    failed: "运行失败",
  }[state];
}

function isReadinessFilter(value: unknown): value is ReadinessFilter {
  return value === "all" || (typeof value === "string" && value in READINESS);
}

function isComparisonFilter(value: unknown): value is ComparisonFilter {
  return (
    value === "all" ||
    value === "labeled" ||
    value === "matched" ||
    value === "mismatched" ||
    value === "unscored" ||
    value === "not_run" ||
    value === "unlabeled"
  );
}

function readStoredFilters(): Partial<CorpusFilterSnapshot> | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(FILTER_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      search: typeof parsed.search === "string" ? parsed.search : undefined,
      readiness: isReadinessFilter(parsed.readiness)
        ? parsed.readiness
        : undefined,
      comparison: isComparisonFilter(parsed.comparison)
        ? parsed.comparison
        : undefined,
      sourceType:
        typeof parsed.sourceType === "string" ? parsed.sourceType : undefined,
      groupId: typeof parsed.groupId === "string" ? parsed.groupId : undefined,
      unprocessedOnly:
        parsed.unprocessedFilterVersion === 2 &&
        typeof parsed.unprocessedOnly === "boolean"
          ? parsed.unprocessedOnly
          : undefined,
    };
  } catch {
    return null;
  }
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function formatDuration(value?: number | null) {
  if (typeof value !== "number") return "-";
  return value < 1_000 ? `${value} ms` : `${(value / 1_000).toFixed(1)} s`;
}

function formatPercent(value?: number | null) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "-";
}

function shortHash(value?: string | null) {
  if (!value) return "-";
  return `${value.slice(0, 10)}...${value.slice(-6)}`;
}

function projectionLabel(
  value: SocCorpusWorkbenchAlert["effective_operational_projection"],
) {
  if (value === "ignore") return "忽略";
  if (value === "transfer") return "转交";
  return "未定";
}

function comparisonLabel(value: SocCorpusComparisonStatus) {
  if (value === "matched") return "一致";
  if (value === "mismatched") return "不一致";
  if (value === "unscored") return "不可评分";
  if (value === "not_run") return "运行后比较";
  return "无标签";
}

function comparisonClass(value: SocCorpusComparisonStatus) {
  if (value === "matched") {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  if (value === "mismatched") {
    return "border-red-300 bg-red-50 text-red-800";
  }
  if (value === "unscored") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  return "border-zinc-300 bg-zinc-50 text-zinc-700";
}

function SummaryBand({ state }: { state: SocCorpusWorkbenchState }) {
  const summary = state.readiness;
  const evaluation = state.evaluation;
  const items = [
    {
      label: "全部样本",
      value: `${summary.total_alert_count} 条`,
      detail: `${state.source.labeled_alert_count} 有处置标签 · ${state.source.unlabeled_alert_count} 无标签`,
    },
    {
      label: "已完成研判",
      value: `${summary.processed_count} 已完成`,
      detail: `${summary.failed_count} 失败 · ${evaluation.processed_labeled_count} 条已比较`,
    },
    {
      label: "基础结论对比",
      value: formatPercent(evaluation.base_match_rate),
      detail: `${evaluation.base_matched_count} 一致 · ${evaluation.base_mismatched_count} 不一致 · ${evaluation.base_unscored_count} 未定`,
    },
    {
      label: "最终结论对比",
      value: formatPercent(evaluation.effective_match_rate),
      detail: `${evaluation.effective_matched_count} 一致 · ${evaluation.effective_mismatched_count} 不一致 · ${evaluation.effective_unscored_count} 未定`,
    },
    {
      label: "历史经验命中",
      value: `${summary.memory_hit_alert_count} 条`,
      detail: "研判时找到相关已审核经验",
    },
    {
      label: "样本时间范围",
      value: `${formatDateTime(state.source.first_event_time)}`,
      detail: `至 ${formatDateTime(state.source.last_event_time)} · 按事件时间升序`,
    },
  ];
  return (
    <section className="grid border-b sm:grid-cols-2 xl:grid-cols-6">
      {items.map((item) => (
        <div
          key={item.label}
          className="min-w-0 border-r border-b px-5 py-4 last:border-r-0 xl:border-b-0"
        >
          <p className="text-muted-foreground truncate text-xs">{item.label}</p>
          <p className="mt-1 text-lg font-semibold tabular-nums">
            {item.value}
          </p>
          <p className="text-muted-foreground mt-1 truncate text-xs">
            {item.detail}
          </p>
        </div>
      ))}
    </section>
  );
}

const EXECUTION_STATUS_LABELS: Record<
  SocCorpusWorkbenchExecution["status"],
  string
> = {
  not_started: "尚未运行",
  running: "运行中",
  analysis_complete: "Runtime 完成，等待 Pattern 写入",
  completed: "完整链路完成",
  failed: "运行失败",
};

const EXECUTION_METRIC_LABELS: Record<string, string> = {
  review_changes: "对象 / 字段调整",
  review_sources: "核对来源",
  adapter: "Adapter",
  canonical_fields: "通用字段",
  missing_fields: "缺失字段",
  unmapped_fields: "未映射字段",
  entity_mentions: "实体提及",
  entity_types: "实体分布",
  scenario_hypotheses: "场景假设",
  role_claims: "角色声明",
  role_resolutions: "角色裁决",
  conflicts: "字段冲突",
  evidence_items: "证据目录",
  context_items: "上下文目录",
  selected_skill_count: "Skills",
  selected_skills: "已选 Skills",
  high_value_gaps: "高价值缺口",
  model: "模型",
  output_quality: "输出质量",
  input_tokens: "输入 Token",
  output_tokens: "输出 Token",
  total_tokens: "总 Token",
  grounded_refs: "有效证据引用",
  rejected_refs: "拒绝证据引用",
  grounded_reasoning: "有效推理引用",
  rejected_reasoning: "拒绝推理引用",
  decision_usable: "决策可用",
  review_required: "需要复核",
  role_verification: "角色复核",
  verdict: "结论",
  confidence: "置信度",
  processing_path: "处理来源",
  matched_rule: "命中规则",
  rule_code: "规则编码",
  disposition: "处置决定",
  memory_id: "复用经验",
  needs_review: "需要复核",
  evidence_state: "证据状态",
  observation_id: "Observation",
  pattern_dimension: "模式维度",
  window_days: "窗口天数",
  support_count: "窗口样本",
  distinct_sources: "独立来源",
  quality_gate_passed: "质量门",
  candidate: "Candidate",
};

function executionStatusIcon(
  status: SocCorpusWorkbenchExecutionPhase["status"],
) {
  if (status === "running") {
    return <LoaderCircleIcon className="size-4 animate-spin text-sky-600" />;
  }
  if (status === "success") {
    return <CheckCircle2Icon className="size-4 text-emerald-600" />;
  }
  if (status === "failed") {
    return <XCircleIcon className="size-4 text-red-600" />;
  }
  return <Clock3Icon className="text-muted-foreground size-4" />;
}

function ExecutionMonitor({
  execution,
  isLoading,
}: {
  execution: SocCorpusWorkbenchExecution | null;
  isLoading: boolean;
}) {
  if (!execution && isLoading) {
    return (
      <section className="border-b px-5 py-4 md:px-7">
        <Skeleton className="h-24 w-full rounded-md" />
      </section>
    );
  }
  if (!execution) return null;
  const completedPhases = execution.phases.filter(
    (phase) => phase.status === "success" || phase.status === "skipped",
  ).length;
  const progress = Math.round(
    (completedPhases / Math.max(execution.phases.length, 1)) * 100,
  );
  return (
    <section className="border-b" aria-label="SOC Runtime 运行轨迹">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 md:px-7">
        <div>
          <div className="flex items-center gap-2">
            <ActivityIcon className="size-4" />
            <h3 className="text-sm font-semibold">运行轨迹 / Runtime Trace</h3>
            <Badge variant="outline">
              {EXECUTION_STATUS_LABELS[execution.status]}
            </Badge>
          </div>
          <p className="text-muted-foreground mt-1 text-xs">
            {execution.run_id ?? "Run 尚未创建"}
            {execution.current_phase
              ? ` · current=${execution.current_phase}`
              : ""}
            {execution.provider_purpose
              ? ` · provider=${execution.provider_purpose}`
              : ""}
          </p>
        </div>
        <div className="text-right text-xs tabular-nums">
          <p className="font-medium">
            {formatDuration(
              execution.elapsed_ms ?? execution.total_duration_ms,
            )}
          </p>
          <p className="text-muted-foreground mt-1">
            {completedPhases}/{execution.phases.length} phases · {progress}%
          </p>
        </div>
      </div>

      <div className="flex min-w-0 items-center gap-1 overflow-x-auto border-y bg-zinc-50 px-5 py-2 md:px-7">
        {execution.phases.map((phase, index) => (
          <div key={phase.phase} className="flex shrink-0 items-center gap-1">
            <div
              className={cn(
                "flex items-center gap-1.5 px-2 py-1 text-xs",
                phase.status === "skipped" && "text-muted-foreground",
              )}
            >
              {executionStatusIcon(phase.status)}
              <span>{phase.label}</span>
              {phase.status === "skipped" ? <span>已跳过</span> : null}
            </div>
            {index + 1 < execution.phases.length ? (
              <ArrowRightIcon className="text-muted-foreground size-3.5" />
            ) : null}
          </div>
        ))}
      </div>

      {execution.execution_options ? (
        <SocRunOptionsSummary value={execution.execution_options} />
      ) : null}

      <div className="divide-y">
        {execution.phases.map((phase) => (
          <div
            key={phase.phase}
            className={cn(
              "grid gap-3 px-5 py-3 md:grid-cols-[210px_minmax(0,1fr)_auto] md:px-7",
              phase.status === "running" && "bg-sky-50",
              phase.status === "failed" && "bg-red-50",
              phase.status === "skipped" && "text-muted-foreground",
            )}
          >
            <div className="flex min-w-0 items-start gap-2">
              <span className="mt-0.5 shrink-0">
                {executionStatusIcon(phase.status)}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium">{phase.label}</p>
                <p className="text-muted-foreground mt-1 text-xs">
                  {phase.status === "skipped" ? "已跳过" : phase.phase}
                </p>
              </div>
            </div>
            <div className="min-w-0">
              <p className="text-sm leading-5">{phase.summary}</p>
              {Object.keys(phase.metrics).length ? (
                <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                  {Object.entries(phase.metrics).map(([key, value]) => (
                    <span key={key} className="min-w-0">
                      <span className="text-muted-foreground">
                        {EXECUTION_METRIC_LABELS[key] ?? key}:{" "}
                      </span>
                      <span className="font-mono break-all">
                        {typeof value === "boolean"
                          ? value
                            ? "yes"
                            : "no"
                          : String(value)}
                      </span>
                    </span>
                  ))}
                </div>
              ) : null}
              {phase.steps.length ? (
                <details className="mt-2 text-xs">
                  <summary className="text-muted-foreground cursor-pointer select-none">
                    {phase.steps.length} 个 Runtime step
                  </summary>
                  <div className="mt-2 divide-y border-l pl-3">
                    {phase.steps.map((step) => (
                      <div
                        key={`${phase.phase}-${step.step_name}`}
                        className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5"
                      >
                        <span className="font-medium">{step.label}</span>
                        <span className="text-muted-foreground font-mono">
                          {step.step_name}
                        </span>
                        <span className="ml-auto tabular-nums">
                          {formatDuration(step.duration_ms)}
                        </span>
                        {step.warning_count ? (
                          <span className="text-amber-700">
                            {step.warning_count} warnings
                          </span>
                        ) : null}
                        {step.error ? (
                          <span className="w-full text-red-700">
                            {step.error}
                          </span>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}
            </div>
            <span className="text-muted-foreground text-right text-xs tabular-nums">
              {formatDuration(phase.duration_ms)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function AlertDetail({
  alert,
  execution,
  executionLoading,
  patternWindowDays,
}: {
  alert: SocCorpusWorkbenchAlert | null;
  execution: SocCorpusWorkbenchExecution | null;
  executionLoading: boolean;
  patternWindowDays: number;
}) {
  const promoteMutation = usePromoteSocRunToMemory();
  const [promotionOpen, setPromotionOpen] = useState(false);
  const [promotionNote, setPromotionNote] = useState("");

  useEffect(() => {
    setPromotionOpen(false);
    setPromotionNote("");
  }, [alert?.alert_id]);

  if (!alert) {
    return (
      <section className="text-muted-foreground px-5 py-10 text-center text-sm">
        选择一条告警查看运行结果
      </section>
    );
  }
  const readiness = READINESS[alert.readiness];
  const candidateId =
    alert.learning?.candidate_id ??
    alert.candidate_id ??
    alert.manual_candidate_id;
  const manualCandidate =
    !!candidateId && candidateId === alert.manual_candidate_id;
  const candidateStatus =
    (manualCandidate
      ? alert.manual_candidate_status
      : alert.candidate_status) ?? "pending_review";
  const candidateNeedsReview = [
    "pending_review",
    "confirmed_candidate",
  ].includes(candidateStatus);
  const memoryUsage = memoryRunUsageCopy(
    alert.memory_contexts.length,
    alert.memory_directive_applied,
  );
  const canPromote =
    !!alert.run_id &&
    alert.workflow_state !== "failed" &&
    (alert.learning
      ? alert.learning.action === "promote"
      : !alert.manual_candidate_id && !alert.candidate_id);
  const handlePromotion = async () => {
    if (!alert.run_id) return;
    const note = promotionNote.trim();
    try {
      const result = await promoteMutation.mutateAsync({
        runId: alert.run_id,
        request: note ? { note } : {},
      });
      if (result.memory_candidate) {
        toast.success(result.learning?.label ?? "经验已进入审核");
        setPromotionOpen(false);
        setPromotionNote("");
      } else {
        toast.warning(
          `未创建 Candidate：${result.memory_admission.reason_codes.join(", ")}`,
        );
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "人工提炼失败");
    }
  };
  return (
    <section className="border-t">
      <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-4 md:px-7">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-mono text-base font-semibold">
              Alert {alert.alert_id}
            </h2>
            <Badge
              variant="outline"
              className={readiness.className}
              title={readiness.title}
            >
              {readiness.label}
            </Badge>
            {alert.memory_directive_applied ? (
              <Badge className="bg-emerald-700">已复用审核结论</Badge>
            ) : null}
          </div>
          <p className="mt-1 text-sm">{alert.rule_name ?? "未命名规则"}</p>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {alert.detection_key ?? "detection key unavailable"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canPromote ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPromotionOpen(true)}
            >
              <BrainCircuitIcon className="size-4" />
              提炼经验
            </Button>
          ) : null}
          {alert.run_id ? (
            <Button
              variant={candidateId ? "outline" : "default"}
              size="sm"
              asChild
            >
              <Link
                href={`/workspace/soc/alerts?run_id=${encodeURIComponent(alert.run_id)}`}
              >
                <ShieldCheckIcon className="size-4" />
                查看研判
              </Link>
            </Button>
          ) : null}
        </div>
      </div>

      {alert.operator_outcome ? (
        <SocCaseOutcomePanel
          outcome={alert.operator_outcome}
          className="border-x-0"
        />
      ) : null}

      {alert.run_id && !alert.operator_outcome ? (
        <p className="text-muted-foreground px-5 py-4 text-sm">
          处理结论暂不可用，请刷新后查看。
        </p>
      ) : null}

      <SocRunMemoryContext
        memories={alert.memory_contexts}
        runId={alert.run_id}
      />

      {alert.learning ? (
        <SocMemoryLearningStatus view={alert.learning} />
      ) : alert.memory_id ? (
        <div
          className="flex flex-wrap items-center justify-between gap-4 border-b border-emerald-300 bg-emerald-50 px-5 py-4 text-emerald-950 md:px-7"
          role="status"
          aria-live="polite"
        >
          <div className="flex min-w-0 items-start gap-3">
            <DatabaseIcon className="mt-0.5 size-5 shrink-0" />
            <div className="min-w-0">
              <p className="font-semibold">同类组已沉淀经验</p>
              <p className="mt-1 text-sm">
                这是该组已审核的经验；本次是否读取、引用，以本次研判记录为准。
              </p>
              <p className="mt-1 font-mono text-xs opacity-70">
                技术编号 {alert.memory_id} ·{" "}
                {alert.memory_status ?? "confirmed"}
              </p>
            </div>
          </div>
          <Button size="sm" asChild>
            <Link
              href={`/workspace/soc/memory/records/${encodeURIComponent(alert.memory_id)}`}
            >
              查看 / 修订 Memory
              <ExternalLinkIcon className="size-3.5" />
            </Link>
          </Button>
        </div>
      ) : candidateId ? (
        <div
          className="flex flex-wrap items-center justify-between gap-4 border-b border-sky-300 bg-sky-50 px-5 py-4 text-sky-950 md:px-7"
          role="status"
          aria-live="polite"
        >
          <div className="flex min-w-0 items-start gap-3">
            <BrainCircuitIcon className="mt-0.5 size-5 shrink-0" />
            <div className="min-w-0">
              <p className="font-semibold">
                {candidateNeedsReview
                  ? manualCandidate
                    ? "人工提炼经验待审核"
                    : "同类经验待审核"
                  : "经验已完成审核"}
              </p>
              <p className="mt-1 text-sm">
                {candidateNeedsReview
                  ? manualCandidate
                    ? "由运营人员主动发起提炼。审核并开放使用后，符合适用条件的新告警也可使用。"
                    : "由同类样本自动提炼。符合适用条件的告警共用这条经验，审核一次即可。"
                  : "这条经验已完成审核，可查看历史记录。"}
              </p>
              <p className="mt-1 font-mono text-xs opacity-70">
                技术编号 {candidateId} · {candidateStatus}
              </p>
            </div>
          </div>
          {candidateNeedsReview ? (
            <Button size="sm" asChild>
              <Link
                href={`/workspace/soc/review/memory-candidates/${encodeURIComponent(candidateId)}`}
              >
                {manualCandidate ? "审核人工提炼经验" : "审核同类经验"}
                <ExternalLinkIcon className="size-3.5" />
              </Link>
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className="grid border-y bg-zinc-50 lg:grid-cols-4">
        <div className="border-r px-5 py-4">
          <p className="text-muted-foreground text-xs">Corpus Similarity</p>
          <p className="mt-1 text-sm font-medium">
            长期同类 {alert.group_alert_count} 条
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            当前 {patternWindowDays}d 窗口 {alert.window_alert_count} 条
          </p>
        </div>
        <div className="border-r px-5 py-4">
          <p className="text-muted-foreground text-xs">处理结论</p>
          <div className="mt-1">
            <SocHandlingBadge
              value={
                alert.operator_outcome?.recommended_handling ??
                alert.effective_operational_projection
              }
              failed={alert.workflow_state === "failed"}
            />
          </div>
          <p className="text-muted-foreground mt-1 text-xs">
            {formatDuration(alert.total_duration_ms)} ·{" "}
            {alert.output_quality ?? "-"}
          </p>
          {alert.replay_of_run_id ? (
            <p
              className="text-muted-foreground mt-1 truncate font-mono text-xs"
              title={alert.replay_of_run_id}
            >
              Replay of {alert.replay_of_run_id}
            </p>
          ) : null}
        </div>
        <div className="border-r px-5 py-4">
          <p className="text-muted-foreground text-xs">Observed Pattern</p>
          <p className="mt-1 text-sm">
            {alert.pattern_support_count ?? 0} 条观察 ·{" "}
            {alert.pattern_distinct_source_count ?? 0} 来源
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            gate{" "}
            {alert.pattern_quality_gate_passed === null ||
            alert.pattern_quality_gate_passed === undefined
              ? "-"
              : alert.pattern_quality_gate_passed
                ? "passed"
                : "withheld"}{" "}
            · {formatPercent(alert.pattern_consistency_ratio)}
          </p>
        </div>
        <div className="px-5 py-4">
          <p className="text-muted-foreground text-xs">历史经验使用</p>
          <p className="mt-1 text-sm font-medium">{memoryUsage.label}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            {memoryUsage.detail}
          </p>
        </div>
      </div>

      <ExecutionMonitor execution={execution} isLoading={executionLoading} />
      <SocCorpusAuditViewer alertId={alert.alert_id} runId={alert.run_id} />

      <details className="border-b">
        <summary className="hover:bg-muted/50 cursor-pointer px-5 py-3 text-sm font-semibold md:px-7">
          历史处置对比（DEV 评测）
        </summary>
        <div className="grid border-t lg:grid-cols-4">
          <div className="border-r px-5 py-4">
            <p className="text-muted-foreground text-xs">历史处置标签</p>
            <p className="mt-1 text-sm font-medium">
              {!alert.operational_label_available
                ? "无标签"
                : alert.operational_label_revealed
                  ? alert.operational_label
                  : "Runtime 运行后揭示"}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              运营结果，不等同于独立技术真值
            </p>
          </div>
          <div className="border-r px-5 py-4">
            <p className="text-muted-foreground text-xs">模型结论对应动作</p>
            <div className="mt-1 flex items-center gap-2">
              <span className="text-sm font-medium">
                {projectionLabel(alert.base_operational_projection)}
              </span>
              <Badge
                variant="outline"
                className={comparisonClass(alert.base_label_comparison)}
              >
                {comparisonLabel(alert.base_label_comparison)}
              </Badge>
            </div>
            <p className="text-muted-foreground mt-1 font-mono text-xs">
              {alert.base_projection_basis ?? "-"}
            </p>
          </div>
          <div className="border-r px-5 py-4">
            <p className="text-muted-foreground text-xs">企业策略后最终动作</p>
            <div className="mt-1 flex items-center gap-2">
              <span className="text-sm font-medium">
                {projectionLabel(alert.effective_operational_projection)}
              </span>
              <Badge
                variant="outline"
                className={comparisonClass(alert.effective_label_comparison)}
              >
                {comparisonLabel(alert.effective_label_comparison)}
              </Badge>
            </div>
            <p className="text-muted-foreground mt-1 font-mono text-xs">
              {alert.effective_projection_basis ?? "-"}
            </p>
          </div>
          <div className="px-5 py-4">
            <p className="text-muted-foreground text-xs">标签时间边界</p>
            <p className="mt-1 text-sm font-medium">
              {alert.label_temporal_status === "valid"
                ? "告警之后形成，可评测"
                : alert.label_temporal_status === "unlabeled"
                  ? "无历史标签"
                  : "时间无效，不计入准确率"}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              {alert.operational_label_revealed &&
              alert.operational_label_observed_at
                ? formatDateTime(alert.operational_label_observed_at)
                : "标签详情尚未揭示"}
            </p>
          </div>
        </div>

        {alert.operational_label_revealed && alert.operational_label_reason ? (
          <div className="border-t bg-sky-50 px-5 py-3 text-sm md:px-7">
            <span className="font-medium">历史处置依据：</span>
            {alert.operational_label_reason}
          </div>
        ) : null}
      </details>

      {alert.failure_message ? (
        <div className="border-b border-red-200 bg-red-50 px-5 py-4 text-sm text-red-900 md:px-7">
          <div className="flex items-start gap-2">
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <span>
              {alert.failure_kind}: {alert.failure_message}
            </span>
          </div>
        </div>
      ) : null}

      <div className="grid border-b lg:grid-cols-2">
        <div className="border-r px-5 py-4 md:px-7">
          <h3 className="text-sm font-semibold">研判结论</h3>
          <p className="mt-3 text-sm leading-6">
            {alert.analysis_summary ?? "尚未运行"}
          </p>
          <p className="text-muted-foreground mt-2 text-sm leading-6">
            {alert.analysis_reason ?? "-"}
          </p>
        </div>
        <div className="px-5 py-4 md:px-7">
          <h3 className="text-sm font-semibold">行为指纹</h3>
          <p className="text-muted-foreground mt-2 font-mono text-xs break-all">
            {alert.behavior_fingerprint ?? "unavailable"}
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {alert.behavior_components.length ? (
              alert.behavior_components.map((component) => (
                <Badge
                  key={component}
                  variant="outline"
                  className="max-w-full font-mono text-[11px] whitespace-normal"
                >
                  {component}
                </Badge>
              ))
            ) : (
              <span className="text-muted-foreground text-sm">无稳定组件</span>
            )}
          </div>
        </div>
      </div>

      {alert.decision_stages.length ? (
        <details className="border-t">
          <summary className="hover:bg-muted/50 cursor-pointer px-5 py-4 text-sm font-semibold md:px-7">
            技术审计：模型、经验与企业规则如何形成最终结果
          </summary>
          <SocDecisionLineageTable stages={alert.decision_stages} />
        </details>
      ) : null}

      <Dialog open={promotionOpen} onOpenChange={setPromotionOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>提前提炼为 Candidate</DialogTitle>
            <DialogDescription>
              系统将保存当前 Run 与 Pattern 快照并创建待审
              Candidate。最终判断、业务事实和适用范围在下一步审核中填写。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <label
              htmlFor="memory-promotion-note"
              className="text-sm font-medium"
            >
              补充说明（可选）
            </label>
            <Textarea
              id="memory-promotion-note"
              value={promotionNote}
              onChange={(event) => setPromotionNote(event.target.value)}
              placeholder="可补充希望审核人重点关注的内容；留空也可继续"
              rows={4}
              maxLength={12_000}
            />
            <p className="text-muted-foreground text-right text-xs tabular-nums">
              {promotionNote.trim().length}/12000
            </p>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPromotionOpen(false)}
              disabled={promoteMutation.isPending}
            >
              取消
            </Button>
            <Button
              onClick={() => void handlePromotion()}
              disabled={promoteMutation.isPending}
            >
              {promoteMutation.isPending ? (
                <LoaderCircleIcon className="size-4 animate-spin" />
              ) : (
                <BrainCircuitIcon className="size-4" />
              )}
              确认提前提炼
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

export function SocCorpusValidationWorkbench() {
  const [runSettings, setRunSettings] =
    useState<SocAnalysisExecutionOptions | null>(null);
  useEffect(() => {
    setRunSettings(readCorpusRunSettings());
  }, []);
  function changeRunSettings(value: SocAnalysisExecutionOptions) {
    setRunSettings(value);
    try {
      window.sessionStorage.setItem(
        RUN_SETTINGS_STORAGE_KEY,
        JSON.stringify(value),
      );
    } catch {
      /* Session storage can be disabled. */
    }
  }
  const [search, setSearch] = useState("");
  const [readiness, setReadiness] = useState<ReadinessFilter>("all");
  const [comparison, setComparison] = useState<ComparisonFilter>("all");
  const [sourceType, setSourceType] = useState("all");
  const [groupId, setGroupId] = useState("all");
  const [groupOrigin, setGroupOrigin] = useState<
    (CorpusFilterSnapshot & { page: number }) | null
  >(null);
  const restoredPage = useRef<number | null>(null);
  const [unprocessedOnly, setUnprocessedOnly] = useState(false);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [focusAlertId, setFocusAlertId] = useState<string | null>(null);
  const [runFeedbackByAlert, setRunFeedbackByAlert] = useState<
    Record<string, RunFeedback>
  >({});
  const [processingAlertIds, setProcessingAlertIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [filtersHydrated, setFiltersHydrated] = useState(false);
  const [page, setPage] = useState(0);
  const detailRef = useRef<HTMLDivElement>(null);
  const completionNoticeRunIds = useRef(new Set<string>());
  const terminalRefreshRunIds = useRef(new Set<string>());
  const deferredSearch = useDeferredValue(search.trim());
  const query = useSocCorpusWorkbench({
    search: deferredSearch || null,
    readiness: readiness === "all" ? null : readiness,
    sourceType: sourceType === "all" ? null : sourceType,
    groupId: groupId === "all" ? null : groupId,
    comparison: comparison === "all" ? null : comparison,
    unprocessedOnly,
    focusAlertId,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const activityQuery = useSocCorpusWorkbenchActivity();
  const processMutation = useProcessSocCorpusWorkbenchAlert();
  const state = query.state;
  const selectedRunSettings = state?.run_controls
    ? availableCorpusRunSettings(
        runSettings ?? state.run_controls.defaults,
        state.run_controls,
      )
    : undefined;
  const locallyRunningAlertIds = useMemo(
    () =>
      new Set(
        Object.values(runFeedbackByAlert)
          .filter((item) => item.status === "running")
          .map((item) => item.alertId),
      ),
    [runFeedbackByAlert],
  );
  const activeExecutionByAlert = useMemo(
    () =>
      new Map(
        (activityQuery.activity?.executions ?? []).map((item) => [
          item.alert_id,
          item,
        ]),
      ),
    [activityQuery.activity?.executions],
  );
  const maxConcurrentExecutions =
    activityQuery.activity?.max_concurrent_executions ?? 1;
  const visibleActiveAlertCount = useMemo(
    () =>
      new Set([...activeExecutionByAlert.keys(), ...locallyRunningAlertIds])
        .size,
    [activeExecutionByAlert, locallyRunningAlertIds],
  );
  const availableExecutionSlots = Math.max(
    0,
    activityQuery.activity
      ? maxConcurrentExecutions - visibleActiveAlertCount
      : Number.POSITIVE_INFINITY,
  );
  const runFeedback = selectedAlertId
    ? (runFeedbackByAlert[selectedAlertId] ?? null)
    : null;
  const selectedAlertIsActive =
    !!selectedAlertId &&
    (locallyRunningAlertIds.has(selectedAlertId) ||
      activeExecutionByAlert.has(selectedAlertId));
  const executionQuery = useSocCorpusWorkbenchExecution(selectedAlertId, {
    live: selectedAlertIsActive,
  });
  const activityAlertKey = useMemo(
    () => Array.from(activeExecutionByAlert.keys()).sort().join(","),
    [activeExecutionByAlert],
  );
  const previousActivityAlertKey = useRef(activityAlertKey);

  useEffect(() => {
    if (previousActivityAlertKey.current === activityAlertKey) return;
    previousActivityAlertKey.current = activityAlertKey;
    void query.refetch();
  }, [activityAlertKey, query]);

  const refetchWorkbench = query.refetch;
  useEffect(() => {
    const execution = executionQuery.execution;
    if (!execution?.run_id || !selectedAlertId) return;
    const feedback = runFeedbackByAlert[selectedAlertId];
    if (
      execution.alert_id !== selectedAlertId ||
      feedback?.status !== "running" ||
      processingAlertIds.has(selectedAlertId) ||
      !["completed", "failed"].includes(execution.status) ||
      (execution.run_id === feedback.baselineRunId &&
        feedback.baselineWorkflowState !== "analysis_only")
    )
      return;
    const key = `${selectedAlertId}:${execution.run_id}:${execution.status}`;
    if (terminalRefreshRunIds.current.has(key)) return;
    terminalRefreshRunIds.current.add(key);
    const finishWithoutRow = () => {
      setRunFeedbackByAlert((current) => {
        if (current[selectedAlertId] !== feedback) return current;
        return {
          ...current,
          [selectedAlertId]: {
            ...feedback,
            status: execution.status === "failed" ? "failed" : "completed",
            message:
              execution.status === "failed"
                ? "本次研判失败，可在运行轨迹中查看原因。"
                : "本次研判已完成，可刷新列表查看结果。",
          },
        };
      });
    };
    // Completion is authoritative even when an activity poll missed the short-lived
    // claim. Fetch the final row once, without accepting an older rerun result.
    void refetchWorkbench().then(({ data }) => {
      const row = data?.alerts.find(
        (item) => item.alert_id === selectedAlertId,
      );
      if (
        row &&
        row.run_id === execution.run_id &&
        ["completed", "failed"].includes(row.workflow_state)
      )
        return;
      finishWithoutRow();
    }, finishWithoutRow);
  }, [
    executionQuery.execution,
    processingAlertIds,
    refetchWorkbench,
    runFeedbackByAlert,
    selectedAlertId,
  ]);

  useEffect(() => {
    if (!state) return;
    const updates: Record<string, RunFeedback> = {};
    for (const [alertId, feedback] of Object.entries(runFeedbackByAlert)) {
      if (
        feedback.status !== "running" ||
        processingAlertIds.has(alertId) ||
        activeExecutionByAlert.has(alertId)
      ) {
        continue;
      }
      const alert = state.alerts.find((item) => item.alert_id === alertId);
      const hasNewRun = alert?.run_id !== feedback.baselineRunId;
      const resumedPattern =
        feedback.baselineWorkflowState === "analysis_only" &&
        alert?.workflow_state === "completed";
      const hasCurrentOutcome = hasNewRun || resumedPattern;
      if (alert?.workflow_state === "completed" && hasCurrentOutcome) {
        updates[alertId] = completedRunFeedback(alert);
        if (alert.run_id && !completionNoticeRunIds.current.has(alert.run_id)) {
          completionNoticeRunIds.current.add(alert.run_id);
          if (alert.learning && alert.learning.action !== "promote") {
            toast.success(alert.learning.label, {
              description: alert.learning.detail,
              duration: 12_000,
            });
          } else if (
            alert.candidate_id &&
            alert.candidate_status === "pending_review" &&
            !alert.memory_id
          ) {
            toast.success("同类经验待审核", {
              description: `已累计 ${alert.pattern_support_count ?? alert.window_alert_count} 条有效观察。`,
              duration: 12_000,
            });
          } else if (alert.memory_id) {
            const memoryUse = memoryRunUsageCopy(
              alert.memory_contexts.length,
              alert.memory_directive_applied,
            );
            toast.success("已采用审核经验", {
              description: `${memoryUse.label}：${memoryUse.detail}`,
              duration: 10_000,
            });
          } else {
            toast.success(`Alert ${alertId} 研判完成`);
          }
        }
      } else if (alert?.workflow_state === "failed" && hasCurrentOutcome) {
        updates[alertId] = {
          alertId,
          status: "failed",
          message: alert.failure_message ?? "告警处理失败",
        };
        if (alert.run_id && !completionNoticeRunIds.current.has(alert.run_id)) {
          completionNoticeRunIds.current.add(alert.run_id);
          toast.error(alert.failure_message ?? `Alert ${alertId} 处理失败`);
        }
      }
    }
    if (Object.keys(updates).length > 0) {
      setRunFeedbackByAlert((current) => ({ ...current, ...updates }));
    }
  }, [activeExecutionByAlert, processingAlertIds, runFeedbackByAlert, state]);

  useEffect(() => {
    const stored = readStoredFilters();
    if (stored) {
      if (stored.search !== undefined) setSearch(stored.search);
      if (stored.readiness !== undefined) setReadiness(stored.readiness);
      if (stored.comparison !== undefined) setComparison(stored.comparison);
      if (stored.sourceType !== undefined) setSourceType(stored.sourceType);
      if (stored.groupId !== undefined) setGroupId(stored.groupId);
      if (stored.unprocessedOnly !== undefined) {
        setUnprocessedOnly(stored.unprocessedOnly);
      }
    }
    setFiltersHydrated(true);
  }, []);

  useEffect(() => {
    if (!filtersHydrated || typeof window === "undefined") return;
    const snapshot: CorpusFilterSnapshot = {
      search,
      readiness,
      comparison,
      sourceType,
      groupId,
      unprocessedOnly,
    };
    try {
      window.sessionStorage.setItem(
        FILTER_STORAGE_KEY,
        JSON.stringify({ ...snapshot, unprocessedFilterVersion: 2 }),
      );
    } catch {
      // Navigation continuity is best-effort; the workbench remains usable.
    }
  }, [
    comparison,
    filtersHydrated,
    groupId,
    readiness,
    search,
    sourceType,
    unprocessedOnly,
  ]);

  const sourceTypes = useMemo(
    () => state?.source_types ?? [],
    [state?.source_types],
  );

  useEffect(() => {
    if (!filtersHydrated || !state) return;
    if (sourceType !== "all" && !sourceTypes.includes(sourceType)) {
      setSourceType("all");
    }
    if (
      groupId !== "all" &&
      !state.groups.some((group) => group.group_id === groupId)
    ) {
      setGroupId("all");
    }
  }, [filtersHydrated, groupId, sourceType, sourceTypes, state]);

  const pageCount = Math.max(
    1,
    Math.ceil((state?.alert_page.total ?? 0) / PAGE_SIZE),
  );
  const pageAlerts = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return (state?.alerts ?? [])
      .filter(
        (alert) =>
          alert.alert_id === focusAlertId ||
          readiness === "all" ||
          alert.readiness === readiness,
      )
      .filter(
        (alert) => sourceType === "all" || alert.source_type === sourceType,
      )
      .filter((alert) => groupId === "all" || alert.group_id === groupId)
      .filter(
        (alert) =>
          !unprocessedOnly ||
          alert.workflow_state !== "completed" ||
          alert.alert_id === focusAlertId,
      )
      .filter((alert) => {
        if (alert.alert_id === focusAlertId) return true;
        if (comparison === "all") return true;
        if (comparison === "labeled") {
          return alert.operational_label_available;
        }
        return alert.effective_label_comparison === comparison;
      })
      .filter((alert) => {
        if (!needle) return true;
        return [
          alert.alert_id,
          alert.rule_code,
          alert.rule_name,
          alert.detection_key,
          alert.endpoint,
          alert.host_name,
          alert.topic,
        ].some((value) => value?.toLocaleLowerCase().includes(needle));
      });
  }, [
    comparison,
    focusAlertId,
    groupId,
    readiness,
    search,
    sourceType,
    state?.alerts,
    unprocessedOnly,
  ]);

  useEffect(() => {
    setPage(restoredPage.current ?? 0);
    restoredPage.current = null;
    setFocusAlertId(null);
  }, [comparison, groupId, readiness, search, sourceType, unprocessedOnly]);

  useEffect(() => {
    if (query.isPlaceholderData || deferredSearch !== search.trim()) return;
    if (page < pageCount) return;
    setPage(Math.max(0, pageCount - 1));
  }, [deferredSearch, page, pageCount, query.isPlaceholderData, search]);

  useEffect(() => {
    if (!filtersHydrated || !state) return;
    if (!selectedAlertId) return;
    if (pageAlerts.some((item) => item.alert_id === selectedAlertId)) return;
    if (
      state.rehearsal_alerts.some((item) => item.alert_id === selectedAlertId)
    ) {
      return;
    }
    setSelectedAlertId(null);
  }, [filtersHydrated, pageAlerts, selectedAlertId, state]);

  const selectedAlert = useMemo(
    () =>
      state?.alerts.find((item) => item.alert_id === selectedAlertId) ??
      state?.rehearsal_alerts.find(
        (item) => item.alert_id === selectedAlertId,
      ) ??
      null,
    [selectedAlertId, state?.alerts, state?.rehearsal_alerts],
  );

  const handleProcess = async (alertId: string) => {
    if (
      locallyRunningAlertIds.has(alertId) ||
      activeExecutionByAlert.has(alertId)
    ) {
      toast.info(`Alert ${alertId} 已在运行，不会重复执行`);
      return;
    }
    setSelectedAlertId(alertId);
    const baselineAlert =
      state?.alerts.find((item) => item.alert_id === alertId) ??
      state?.rehearsal_alerts.find((item) => item.alert_id === alertId);
    setProcessingAlertIds((current) => {
      const next = new Set(current);
      next.add(alertId);
      return next;
    });
    setRunFeedbackByAlert((current) => ({
      ...current,
      [alertId]: {
        alertId,
        status: "running",
        message: "正在执行归一化、模型研判、决策与经验匹配。",
        baselineRunId: baselineAlert?.run_id ?? null,
        baselineWorkflowState: baselineAlert?.workflow_state ?? "ready",
      },
    }));
    try {
      const settings = selectedRunSettings;
      await processMutation.mutateAsync({ alertId, settings });
      setFocusAlertId(alertId);
      setRunFeedbackByAlert((current) => ({
        ...current,
        [alertId]: {
          ...current[alertId],
          alertId,
          status: "running",
          message: "后台已受理，运行轨迹将持续更新；离开页面不会中断本次研判。",
        },
      }));
      toast.success(`Alert ${alertId} 已开始研判`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "告警处理失败";
      if (error instanceof SocApiError && error.status === 409) {
        const refreshed = await activityQuery.refetch();
        const duplicateIsActive = refreshed.data?.executions.some(
          (item) => item.alert_id === alertId,
        );
        if (duplicateIsActive) {
          setRunFeedbackByAlert((current) => ({
            ...current,
            [alertId]: {
              alertId,
              status: "running",
              message: "该告警已由另一会话开始研判，本次点击未重复执行。",
            },
          }));
          toast.info(`Alert ${alertId} 已由另一会话运行`);
        } else {
          setRunFeedbackByAlert((current) => ({
            ...current,
            [alertId]: {
              alertId,
              status: "failed",
              message: "当前并发槽位已满，请等待任一告警完成后重试。",
            },
          }));
          toast.warning("当前并发槽位已满");
        }
      } else {
        setRunFeedbackByAlert((current) => ({
          ...current,
          [alertId]: {
            alertId,
            status: "failed",
            message,
          },
        }));
        toast.error(message);
      }
    } finally {
      setProcessingAlertIds((current) => {
        const next = new Set(current);
        next.delete(alertId);
        return next;
      });
    }
  };

  const handleViewResult = (alertId: string) => {
    setSelectedAlertId(alertId);
    requestAnimationFrame(() => {
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  const handleSelectDemoTarget = (target: SocLeadershipDemoTarget) => {
    if (!target.actual_group_id || target.availability !== "ready") return;
    setGroupOrigin(null);
    setSearch("");
    setReadiness("all");
    setComparison("all");
    setSourceType(target.source_type);
    setGroupId(target.actual_group_id);
    setUnprocessedOnly(false);
    setSelectedAlertId(target.primary_alert_id);
    setPage(0);
  };

  const handleOpenGroup = (nextGroupId: string) => {
    if (nextGroupId === "all") {
      setGroupId("all");
      setGroupOrigin(null);
      return;
    }
    setGroupOrigin(
      (current) =>
        current ?? {
          search,
          readiness,
          comparison,
          sourceType,
          groupId,
          unprocessedOnly,
          page,
        },
    );
    setSearch("");
    setReadiness("all");
    setComparison("all");
    setSourceType("all");
    setUnprocessedOnly(false);
    setGroupId(nextGroupId);
    setSelectedAlertId(null);
    setPage(0);
  };

  const handleReturnFromGroup = () => {
    if (!groupOrigin) return;
    const filtersChanged =
      search !== groupOrigin.search ||
      readiness !== groupOrigin.readiness ||
      comparison !== groupOrigin.comparison ||
      sourceType !== groupOrigin.sourceType ||
      groupId !== groupOrigin.groupId ||
      unprocessedOnly !== groupOrigin.unprocessedOnly;
    restoredPage.current = filtersChanged ? groupOrigin.page : null;
    setSearch(groupOrigin.search);
    setReadiness(groupOrigin.readiness);
    setComparison(groupOrigin.comparison);
    setSourceType(groupOrigin.sourceType);
    setGroupId(groupOrigin.groupId);
    setUnprocessedOnly(groupOrigin.unprocessedOnly);
    setPage(groupOrigin.page);
    setSelectedAlertId(null);
    setGroupOrigin(null);
  };
  const selectedGroup = state?.groups.find(
    (group) => group.group_id === groupId,
  );

  if (query.isLoading && !state) {
    return (
      <div className="flex size-full min-h-0 flex-col">
        <SocWorkspaceHeader
          icon={FileSearchIcon}
          title="SOC 告警研判演练"
          description="选择历史告警，观察完整研判、经验参考与结论复用"
        />
        <div className="space-y-4 p-6" aria-label="正在加载 SOC 告警研判演练">
          <Skeleton className="h-28 w-full rounded-md" />
          <Skeleton className="h-96 w-full rounded-md" />
        </div>
      </div>
    );
  }

  if (query.error && !state) {
    return (
      <div className="flex size-full min-h-0 flex-col">
        <SocWorkspaceHeader
          icon={FileSearchIcon}
          title="SOC 告警研判演练"
          description="选择历史告警，观察完整研判、经验参考与结论复用"
        />
        <main className="flex flex-1 items-center justify-center p-6">
          <div className="max-w-xl border border-amber-200 bg-amber-50 px-5 py-4 text-amber-900">
            <div className="flex items-start gap-3">
              <AlertTriangleIcon className="mt-0.5 size-5 shrink-0" />
              <div>
                <h2 className="font-medium">DEV 工作台不可用</h2>
                <p className="mt-2 text-sm leading-6">
                  {query.error instanceof Error
                    ? query.error.message
                    : "后端未启用本地告警演练工作台。"}
                </p>
              </div>
            </div>
          </div>
        </main>
      </div>
    );
  }

  if (!state) return null;
  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={FileSearchIcon}
        title="SOC 告警研判演练"
        description="选择历史告警，观察完整研判、经验参考与结论复用"
        actions={
          <>
            <Badge
              variant="outline"
              className="border-sky-300 bg-sky-50 text-sky-800"
            >
              DEV
            </Badge>
            <Badge variant="outline">
              <DatabaseIcon className="size-3.5" />
              SQLite · {state.safety.database_file}
            </Badge>
            <Badge
              variant="outline"
              className={cn(
                visibleActiveAlertCount
                  ? "border-sky-300 bg-sky-50 text-sky-800"
                  : "border-zinc-300 bg-white text-zinc-700",
              )}
              title="不同告警可并行；同一告警只允许一个活动执行"
            >
              <ActivityIcon className="size-3.5" />
              运行中 {visibleActiveAlertCount}/{maxConcurrentExecutions}
            </Badge>
            <Button
              variant="outline"
              size="icon-sm"
              onClick={() => void query.refetch()}
              disabled={query.isFetching}
              aria-label="刷新告警演练状态"
              title="刷新告警演练状态"
            >
              <RefreshCwIcon
                className={cn("size-4", query.isFetching && "animate-spin")}
              />
            </Button>
          </>
        }
      />

      <main className="min-h-0 flex-1 overflow-y-auto">
        <section className="flex flex-wrap items-center justify-between gap-3 border-b bg-zinc-50 px-5 py-3 text-xs md:px-7">
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            <span className="font-medium text-amber-800">
              历史告警样本 · 非生产演练
            </span>
            <span>可任意选择 · 可重新运行</span>
            <span>企业安全能力接口 · 关闭/模拟</span>
            {!state.run_controls ? (
              <>
                <span className="font-medium text-sky-800">
                  语义核对 ·{" "}
                  {state.safety.normalization_review_mode === "shadow"
                    ? "仅对比，未用于研判"
                    : state.safety.normalization_review_mode === "apply"
                      ? "用于后续研判"
                      : "未开启"}
                </span>
                <span>
                  企业专属策略 ·{" "}
                  {formatSocDevPolicyLabel({
                    tenantPolicy: state.safety.tenant_policy,
                    softwarePathFastPolicy:
                      state.safety.software_path_fast_policy,
                  })}
                </span>
              </>
            ) : null}
            <span>外部动作 · 关闭</span>
          </div>
          <span className="font-mono">
            {state.source.file_name} · {shortHash(state.source.sha256)}
          </span>
        </section>

        {state.run_controls && selectedRunSettings ? (
          <SocCorpusRunSettings
            controls={state.run_controls}
            value={selectedRunSettings}
            onChange={changeRunSettings}
          />
        ) : null}

        <SocLeadershipDemoGuidePanel
          guide={state.leadership_demo}
          groups={state.groups}
          alerts={state.rehearsal_alerts}
          activeGroupId={groupId}
          onSelectTarget={handleSelectDemoTarget}
        />

        <SummaryBand state={state} />

        <section className="border-b px-5 py-4 md:px-7">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-56 flex-1">
              <label
                htmlFor="corpus-search"
                className="mb-1.5 block text-xs font-medium"
              >
                搜索
              </label>
              <div className="relative">
                <SearchIcon className="text-muted-foreground pointer-events-none absolute top-2.5 left-3 size-4" />
                <Input
                  id="corpus-search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="告警编号 / 规则 / 主机 / IP"
                  className="pl-9"
                />
              </div>
            </div>
            <div className="w-44">
              <label
                htmlFor="corpus-readiness-filter"
                className="mb-1.5 block text-xs font-medium"
              >
                经验分组质量
              </label>
              <Select
                value={readiness}
                onValueChange={(value) =>
                  setReadiness(value as ReadinessFilter)
                }
              >
                <SelectTrigger id="corpus-readiness-filter" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部质量</SelectItem>
                  {Object.entries(READINESS).map(([value, item]) => (
                    <SelectItem key={value} value={value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="w-36">
              <label
                htmlFor="corpus-source-filter"
                className="mb-1.5 block text-xs font-medium"
              >
                来源
              </label>
              <Select value={sourceType} onValueChange={setSourceType}>
                <SelectTrigger id="corpus-source-filter" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部来源</SelectItem>
                  {sourceTypes.map((value) => (
                    <SelectItem key={value} value={value}>
                      {value.toUpperCase()}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="w-36">
              <label
                htmlFor="corpus-comparison-filter"
                className="mb-1.5 block text-xs font-medium"
              >
                与历史处置比较
              </label>
              <Select
                value={comparison}
                onValueChange={(value) =>
                  setComparison(value as ComparisonFilter)
                }
              >
                <SelectTrigger id="corpus-comparison-filter" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部结果</SelectItem>
                  <SelectItem value="labeled">有历史标签</SelectItem>
                  <SelectItem value="matched">一致</SelectItem>
                  <SelectItem value="mismatched">不一致</SelectItem>
                  <SelectItem value="unscored">不可评分</SelectItem>
                  <SelectItem value="not_run">尚未运行</SelectItem>
                  <SelectItem value="unlabeled">无标签</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="min-w-0 grow basis-64">
              <label
                htmlFor="corpus-group-filter"
                className="mb-1.5 block text-xs font-medium"
              >
                行为模式组
              </label>
              <SocCorpusGroupPicker
                groups={state.groups}
                value={groupId}
                onValueChange={handleOpenGroup}
              />
            </div>
            <label className="flex h-9 items-center gap-2 border px-3 text-sm">
              <Switch
                checked={unprocessedOnly}
                onCheckedChange={setUnprocessedOnly}
                aria-label="仅显示未运行告警"
              />
              仅未运行
            </label>
            <Button
              variant="ghost"
              size="icon-sm"
              title="重置筛选"
              aria-label="重置筛选"
              onClick={() => {
                setSearch("");
                setReadiness("all");
                setComparison("all");
                setSourceType("all");
                setGroupId("all");
                setGroupOrigin(null);
                setUnprocessedOnly(false);
              }}
            >
              <RotateCcwIcon className="size-4" />
            </Button>
          </div>
          {selectedGroup && (
            <div
              className="mt-3 flex flex-wrap items-center gap-3 border-l-2 border-sky-600 bg-sky-50 px-3 py-2 text-sm"
              aria-label="当前行为模式组"
            >
              <ListFilterIcon className="size-4 shrink-0 text-sky-700" />
              <span className="min-w-0 flex-1 basis-48 break-words">
                {formatCorpusGroupOption(selectedGroup)}
              </span>
              {groupOrigin && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleReturnFromGroup}
                >
                  <ArrowLeftIcon className="size-4" />
                  返回原筛选
                </Button>
              )}
            </div>
          )}
          <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-2 text-xs">
            <FilterIcon className="size-3.5" />
            <span>{state.alert_page.total} 条命中筛选</span>
            <span>·</span>
            <span>筛选条件会在当前浏览器标签页保留</span>
            <span>·</span>
            <span>分组质量不代表研判准确率或经验候选已经审核通过</span>
          </div>
        </section>

        <section className="border-b">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1320px] table-fixed text-left text-sm">
              <thead className="bg-zinc-50 text-xs">
                <tr>
                  <th className="w-32 px-4 py-2.5 font-medium">序号 / Alert</th>
                  <th className="w-36 px-4 py-2.5 font-medium">时间 / 来源</th>
                  <th className="w-72 px-4 py-2.5 font-medium">规则</th>
                  <th className="w-40 px-4 py-2.5 font-medium">分组质量</th>
                  <th className="w-36 px-4 py-2.5 font-medium">处理结论</th>
                  <th className="w-44 px-4 py-2.5 font-medium">历史处置对比</th>
                  <th className="w-32 px-4 py-2.5 font-medium">模式样本</th>
                  <th className="w-32 px-4 py-2.5 font-medium">历史经验</th>
                  <th className="w-44 px-4 py-2.5 text-right font-medium">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {pageAlerts.map((alert) => {
                  const readinessItem = READINESS[alert.readiness];
                  const localProcessing =
                    processingAlertIds.has(alert.alert_id) ||
                    locallyRunningAlertIds.has(alert.alert_id);
                  const activeExecution =
                    activeExecutionByAlert.get(alert.alert_id) ??
                    alert.active_execution ??
                    null;
                  const processing =
                    localProcessing ||
                    !!activeExecution ||
                    alert.workflow_state === "running";
                  const otherSessionProcessing = !localProcessing && processing;
                  const executionCapacityFull = availableExecutionSlots === 0;
                  return (
                    <tr
                      key={alert.alert_id}
                      data-alert-id={alert.alert_id}
                      className={cn(
                        "cursor-pointer border-t align-top hover:bg-zinc-50",
                        selectedAlertId === alert.alert_id && "bg-sky-50/70",
                      )}
                      onClick={() => setSelectedAlertId(alert.alert_id)}
                    >
                      <td className="px-4 py-3 font-mono text-xs">
                        <p className="text-muted-foreground tabular-nums">
                          #{alert.sequence_number}
                        </p>
                        <p className="mt-1">{alert.alert_id}</p>
                      </td>
                      <td className="px-4 py-3">
                        <p className="tabular-nums">
                          {formatDateTime(alert.observed_at)}
                        </p>
                        <p className="text-muted-foreground mt-1 text-xs">
                          {alert.source_type.toUpperCase()} ·{" "}
                          {alert.topic ?? "-"}
                        </p>
                      </td>
                      <td className="px-4 py-3">
                        <p
                          className="truncate"
                          title={alert.rule_name ?? undefined}
                        >
                          {alert.rule_name ?? "-"}
                        </p>
                        <p className="text-muted-foreground mt-1 truncate font-mono text-xs">
                          {alert.rule_code ?? alert.detection_key ?? "-"}
                        </p>
                        <Button
                          size="sm"
                          variant="outline"
                          className="mt-2 h-7 text-xs"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleOpenGroup(alert.group_id);
                          }}
                        >
                          <ListFilterIcon className="size-3.5" />
                          查看同组 · {alert.group_alert_count} 条
                        </Button>
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          variant="outline"
                          className={readinessItem.className}
                          title={readinessItem.title}
                        >
                          {readinessItem.label}
                        </Badge>
                        <p className="text-muted-foreground mt-1 text-xs tabular-nums">
                          同类 {alert.group_alert_count} ·{" "}
                          {state.safety.pattern_window_days}d{" "}
                          {alert.window_alert_count}
                        </p>
                      </td>
                      <td className="px-4 py-3">
                        <SocHandlingBadge
                          value={
                            alert.operator_outcome?.recommended_handling ??
                            alert.effective_operational_projection
                          }
                          failed={alert.workflow_state === "failed"}
                        />
                        <p
                          className={cn(
                            "mt-1 text-xs",
                            processing
                              ? "font-medium text-sky-700"
                              : "text-muted-foreground",
                          )}
                          title={
                            activeExecution
                              ? `开始于 ${formatDateTime(activeExecution.started_at)} · ${activeExecution.actor_id}`
                              : undefined
                          }
                        >
                          {processing
                            ? otherSessionProcessing
                              ? "其他会话运行中"
                              : "本会话运行中"
                            : workflowStateLabel(alert.workflow_state)}
                        </p>
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          variant="outline"
                          className={comparisonClass(
                            alert.effective_label_comparison,
                          )}
                        >
                          {comparisonLabel(alert.effective_label_comparison)}
                        </Badge>
                        <p className="text-muted-foreground mt-1 text-xs">
                          {alert.operational_label_revealed
                            ? `本次 ${projectionLabel(alert.effective_operational_projection)} · 历史 ${alert.operational_label ?? "-"}`
                            : alert.operational_label_available
                              ? "标签待揭示"
                              : "无历史标签"}
                        </p>
                      </td>
                      <td className="px-4 py-3 tabular-nums">
                        {typeof alert.pattern_support_count === "number" ? (
                          <>
                            <p>{alert.pattern_support_count} 条</p>
                            <p className="text-muted-foreground mt-1 text-xs">
                              {alert.pattern_distinct_source_count ?? 0} 来源
                            </p>
                          </>
                        ) : (
                          "-"
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-medium">
                          {
                            memoryRunUsageCopy(
                              alert.memory_contexts.length,
                              alert.memory_directive_applied,
                            ).label
                          }
                        </p>
                        {alert.memory_contexts.length ? (
                          <p className="text-muted-foreground mt-1 text-xs">
                            参考 {alert.memory_contexts.length} 条经验
                          </p>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {processing ? (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled
                            title={
                              otherSessionProcessing
                                ? "该告警已由另一会话占用，本次不会重复执行"
                                : "当前浏览器会话正在执行该告警"
                            }
                          >
                            <RefreshCwIcon className="size-4 animate-spin" />
                            {otherSessionProcessing
                              ? "其他会话运行中"
                              : "本会话运行中"}
                          </Button>
                        ) : alert.workflow_state === "completed" ? (
                          <div className="flex justify-end gap-1">
                            <Button
                              size="sm"
                              variant="outline"
                              aria-label={`查看 Alert ${alert.alert_id} 结果`}
                              onClick={(event) => {
                                event.stopPropagation();
                                handleViewResult(alert.alert_id);
                              }}
                            >
                              <EyeIcon className="size-4" />
                              查看结果
                            </Button>
                            <Button
                              size="icon-sm"
                              variant="ghost"
                              disabled={
                                !alert.can_process || executionCapacityFull
                              }
                              title={
                                executionCapacityFull
                                  ? "当前并发槽位已满"
                                  : "创建新 Run；不重复累计同一告警的模式样本"
                              }
                              aria-label={`重新运行 Alert ${alert.alert_id}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                void handleProcess(alert.alert_id);
                              }}
                            >
                              <RotateCcwIcon className="size-4" />
                            </Button>
                          </div>
                        ) : (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={
                              !alert.can_process || executionCapacityFull
                            }
                            title={
                              executionCapacityFull
                                ? "当前并发槽位已满"
                                : undefined
                            }
                            onClick={(event) => {
                              event.stopPropagation();
                              void handleProcess(alert.alert_id);
                            }}
                          >
                            {alert.workflow_state === "failed" ? (
                              <RotateCcwIcon className="size-4" />
                            ) : (
                              <PlayIcon className="size-4" />
                            )}
                            {alert.workflow_state === "failed"
                              ? "重试"
                              : "运行"}
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!pageAlerts.length ? (
            <div className="text-muted-foreground px-5 py-12 text-center text-sm">
              当前筛选没有告警
            </div>
          ) : null}
          <div className="flex items-center justify-between border-t px-5 py-3 text-sm md:px-7">
            <span className="text-muted-foreground tabular-nums">
              第 {page + 1}/{pageCount} 页
            </span>
            <div className="flex gap-1">
              <Button
                variant="outline"
                size="icon-sm"
                disabled={page === 0}
                onClick={() => setPage((value) => Math.max(0, value - 1))}
                aria-label="上一页"
                title="上一页"
              >
                <ChevronLeftIcon className="size-4" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                disabled={page + 1 >= pageCount}
                onClick={() =>
                  setPage((value) => Math.min(pageCount - 1, value + 1))
                }
                aria-label="下一页"
                title="下一页"
              >
                <ChevronRightIcon className="size-4" />
              </Button>
            </div>
          </div>
        </section>

        {runFeedback ? (
          <section
            className={cn(
              "flex flex-wrap items-center justify-between gap-3 border-b px-5 py-3 text-sm md:px-7",
              runFeedback.status === "completed" &&
                "border-emerald-200 bg-emerald-50 text-emerald-950",
              runFeedback.status === "running" &&
                "border-sky-200 bg-sky-50 text-sky-950",
              runFeedback.status === "failed" &&
                "border-red-200 bg-red-50 text-red-950",
            )}
            aria-live="polite"
          >
            <div className="flex min-w-0 items-start gap-2">
              {runFeedback.status === "running" ? (
                <LoaderCircleIcon className="mt-0.5 size-4 shrink-0 animate-spin" />
              ) : runFeedback.status === "completed" ? (
                <CheckCircle2Icon className="mt-0.5 size-4 shrink-0" />
              ) : (
                <XCircleIcon className="mt-0.5 size-4 shrink-0" />
              )}
              <div className="min-w-0">
                <p className="font-medium">
                  Alert {runFeedback.alertId}{" "}
                  {runFeedback.status === "running"
                    ? "正在研判"
                    : runFeedback.status === "completed"
                      ? "研判完成"
                      : "运行失败"}
                </p>
                <p className="mt-0.5 text-xs leading-5 opacity-80">
                  {runFeedback.message}
                </p>
              </div>
            </div>
            {runFeedback.status === "completed" ? (
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`查看 Alert ${runFeedback.alertId} 结果`}
                  onClick={() => handleViewResult(runFeedback.alertId)}
                >
                  <EyeIcon className="size-4" />
                  查看结果
                </Button>
              </div>
            ) : null}
          </section>
        ) : null}

        <div ref={detailRef}>
          <AlertDetail
            alert={selectedAlert}
            execution={executionQuery.execution}
            executionLoading={executionQuery.isLoading}
            patternWindowDays={state.safety.pattern_window_days}
          />
        </div>

        <section className="text-muted-foreground flex flex-wrap items-center gap-x-5 gap-y-2 border-t bg-zinc-50 px-5 py-3 text-xs md:px-7">
          <span className="flex items-center gap-1.5">
            <Clock3Icon className="size-3.5" />
            列表按事件时间展示 · 点击顺序不受限制
          </span>
          <span>交互结果不用于时间因果评测</span>
          <span>Pattern 聚合窗口 {state.safety.pattern_window_days}d</span>
          <span>模型 {state.model.model_name ?? "-"}</span>
          <span>Thinking {state.model.thinking_enabled ? "on" : "off"}</span>
          <span>
            Role verifier {state.model.role_verifier_enabled ? "on" : "off"}
          </span>
          <span className="ml-auto">
            <ExternalLinkIcon className="mr-1 inline size-3.5" />
            运行结果写入当前隔离 SQLite
          </span>
        </section>
      </main>
    </div>
  );
}
