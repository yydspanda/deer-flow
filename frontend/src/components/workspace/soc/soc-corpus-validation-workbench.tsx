"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowRightIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Clock3Icon,
  DatabaseIcon,
  ExternalLinkIcon,
  EyeIcon,
  FilePenLineIcon,
  FileSearchIcon,
  FilterIcon,
  LoaderCircleIcon,
  PlayIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchIcon,
  ShieldCheckIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
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
import { SocCorpusAuditViewer } from "@/components/workspace/soc/soc-corpus-audit-viewer";
import { formatSocDevPolicyLabel } from "@/components/workspace/soc/soc-dev-policy-label";
import { SocLeadershipDemoGuidePanel } from "@/components/workspace/soc/soc-leadership-demo-guide";
import { memoryRunUsageCopy } from "@/components/workspace/soc/soc-memory-copy";
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
  SocCorpusComparisonStatus,
  SocCorpusWorkbenchAlert,
  SocCorpusWorkbenchExecution,
  SocCorpusWorkbenchExecutionPhase,
  SocCorpusWorkbenchReadiness,
  SocCorpusWorkbenchState,
  SocLeadershipDemoTarget,
  SocVerdict,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;
const FILTER_STORAGE_KEY = "soc.corpus-validation.filters.v1";

const VERDICT_LABELS: Record<SocVerdict, string> = {
  true_positive: "真实风险",
  suspicious: "可疑",
  false_positive: "误报",
  unknown: "未知",
  needs_review: "需复核",
};

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
  selectedAlertId: string | null;
}

interface RunFeedback {
  alertId: string;
  status: "running" | "completed" | "failed";
  message: string;
  action?: {
    href: string;
    label: string;
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
        typeof parsed.unprocessedOnly === "boolean"
          ? parsed.unprocessedOnly
          : undefined,
      selectedAlertId:
        typeof parsed.selectedAlertId === "string" ||
        parsed.selectedAlertId === null
          ? parsed.selectedAlertId
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

function verdictLabel(value?: SocVerdict | null) {
  return value ? VERDICT_LABELS[value] : "-";
}

function verdictClass(value?: SocVerdict | null) {
  if (value === "true_positive") {
    return "border-red-300 bg-red-50 text-red-800";
  }
  if (value === "suspicious" || value === "needs_review") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  if (value === "false_positive") {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  return "border-zinc-300 bg-zinc-50 text-zinc-700";
}

function verdictRowClass(value?: SocVerdict | null) {
  if (value === "true_positive") return "border-l-4 border-l-red-500";
  if (value === "suspicious" || value === "needs_review") {
    return "border-l-4 border-l-amber-500";
  }
  if (value === "false_positive") {
    return "border-l-4 border-l-emerald-500";
  }
  return "border-l-4 border-l-zinc-300";
}

function decisionPresentation(value?: SocVerdict | null) {
  if (value === "true_positive") {
    return {
      title: "真实风险 / True Positive",
      description: "当前有效决策认为告警代表真实安全风险。",
      className: "border-red-300 bg-red-50 text-red-950",
      icon: AlertTriangleIcon,
    };
  }
  if (value === "suspicious") {
    return {
      title: "可疑风险 / Suspicious",
      description: "当前有效决策认为存在风险迹象，但仍有证据缺口。",
      className: "border-amber-300 bg-amber-50 text-amber-950",
      icon: AlertTriangleIcon,
    };
  }
  if (value === "false_positive") {
    return {
      title: "误报 / False Positive",
      description: "当前有效决策认为该告警不代表真实攻击。",
      className: "border-emerald-300 bg-emerald-50 text-emerald-950",
      icon: ShieldCheckIcon,
    };
  }
  if (value === "needs_review") {
    return {
      title: "需要复核 / Needs Review",
      description: "当前证据尚不足以形成可直接复用的安全结论。",
      className: "border-amber-300 bg-amber-50 text-amber-950",
      icon: AlertTriangleIcon,
    };
  }
  return {
    title: "尚未定性 / Unknown",
    description: "尚未运行，或当前结果未形成明确安全结论。",
    className: "border-zinc-300 bg-zinc-50 text-zinc-900",
    icon: ActivityIcon,
  };
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
            <div className="flex items-center gap-1.5 px-2 py-1 text-xs">
              {executionStatusIcon(phase.status)}
              <span>{phase.label}</span>
            </div>
            {index + 1 < execution.phases.length ? (
              <ArrowRightIcon className="text-muted-foreground size-3.5" />
            ) : null}
          </div>
        ))}
      </div>

      <div className="divide-y">
        {execution.phases.map((phase) => (
          <div
            key={phase.phase}
            className={cn(
              "grid gap-3 px-5 py-3 md:grid-cols-[210px_minmax(0,1fr)_auto] md:px-7",
              phase.status === "running" && "bg-sky-50",
              phase.status === "failed" && "bg-red-50",
            )}
          >
            <div className="flex min-w-0 items-start gap-2">
              <span className="mt-0.5 shrink-0">
                {executionStatusIcon(phase.status)}
              </span>
              <div className="min-w-0">
                <p className="text-sm font-medium">{phase.label}</p>
                <p className="text-muted-foreground mt-1 font-mono text-xs">
                  {phase.phase}
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
  const candidateId = alert.manual_candidate_id ?? alert.candidate_id;
  const candidateStatus =
    alert.manual_candidate_status ?? alert.candidate_status ?? "pending_review";
  const candidateKind = alert.manual_candidate_id ? "人工提炼" : "同类模式";
  const candidateNeedsReview = candidateStatus === "pending_review";
  const decision = decisionPresentation(alert.effective_verdict);
  const memoryUsage = memoryRunUsageCopy(
    alert.memory_contexts.length,
    alert.memory_directive_applied,
  );
  const DecisionIcon = decision.icon;
  const tenantPolicyStage = alert.decision_stages.find(
    (stage) => stage.stage === "tenant_policy" && stage.status === "applied",
  );
  const operationalActionChanged =
    alert.base_operational_projection !== "undetermined" &&
    alert.effective_operational_projection !== "undetermined" &&
    alert.base_operational_projection !==
      alert.effective_operational_projection;
  const canPromote =
    !!alert.run_id &&
    alert.workflow_state !== "failed" &&
    !alert.manual_candidate_id &&
    !alert.candidate_id;
  const handlePromotion = async () => {
    if (!alert.run_id) return;
    const note = promotionNote.trim();
    try {
      const result = await promoteMutation.mutateAsync({
        runId: alert.run_id,
        request: note ? { note } : {},
      });
      if (result.memory_candidate) {
        toast.success(
          `已创建待审 Candidate ${result.memory_candidate.candidate_id}`,
        );
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
              提炼 Candidate
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

      {alert.run_id ? (
        <div
          className={cn(
            "flex flex-wrap items-center justify-between gap-4 border-y px-5 py-4 md:px-7",
            decision.className,
          )}
          aria-label="当前安全结论"
        >
          <div className="flex min-w-0 items-start gap-3">
            <DecisionIcon className="mt-0.5 size-6 shrink-0" />
            <div className="min-w-0">
              <p className="text-xs font-medium">
                当前安全结论 / Effective Decision
              </p>
              <p className="mt-1 text-xl font-semibold">{decision.title}</p>
              <p className="mt-1 text-sm">{decision.description}</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge
              variant="outline"
              className={verdictClass(alert.base_verdict)}
            >
              Base：{verdictLabel(alert.base_verdict)}
            </Badge>
            <ArrowRightIcon className="size-3.5" />
            <Badge
              variant="outline"
              className={verdictClass(alert.effective_verdict)}
            >
              Effective：{verdictLabel(alert.effective_verdict)}
            </Badge>
            <Badge variant="outline">
              置信度 {formatPercent(alert.effective_confidence)}
            </Badge>
            {alert.effective_needs_review ? (
              <Badge className="bg-amber-700 text-white">需要人工复核</Badge>
            ) : (
              <Badge className="bg-emerald-700 text-white">无需强制复核</Badge>
            )}
          </div>
        </div>
      ) : null}

      {operationalActionChanged ? (
        <div className="border-b border-amber-300 bg-amber-50 px-5 py-4 text-amber-950 md:px-7">
          <div className="flex items-start gap-3">
            <AlertTriangleIcon className="mt-0.5 size-5 shrink-0" />
            <div className="min-w-0">
              <p className="font-semibold">企业策略调整了运营动作</p>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                <Badge
                  variant="outline"
                  className={verdictClass(alert.base_verdict)}
                >
                  模型判断：{verdictLabel(alert.base_verdict)}
                </Badge>
                <ArrowRightIcon className="size-3.5" />
                <Badge variant="outline">
                  基础动作：{projectionLabel(alert.base_operational_projection)}
                </Badge>
                <ArrowRightIcon className="size-3.5" />
                <Badge className="bg-amber-700 text-white">
                  最终动作：
                  {projectionLabel(alert.effective_operational_projection)}
                </Badge>
              </div>
              <p className="mt-2 text-sm leading-6">
                {tenantPolicyStage?.summary ??
                  "受治理策略改变了运营处置，但没有改写模型的技术判断。"}
              </p>
            </div>
          </div>
        </div>
      ) : null}

      {alert.memory_id ? (
        <div
          className="flex flex-wrap items-center justify-between gap-4 border-b border-emerald-300 bg-emerald-50 px-5 py-4 text-emerald-950 md:px-7"
          role="status"
          aria-live="polite"
        >
          <div className="flex min-w-0 items-start gap-3">
            <DatabaseIcon className="mt-0.5 size-5 shrink-0" />
            <div className="min-w-0">
              <p className="font-semibold">已关联一条审核通过的经验</p>
              <p className="mt-1 text-sm">
                本次研判可查看其业务结论、使用历史，或在发现不适用时发起修订。
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
                {candidateNeedsReview ? "同类经验待审核" : "同类经验已完成审核"}
              </p>
              <p className="mt-1 text-sm">
                {candidateNeedsReview
                  ? `${candidateKind}模式已达到沉淀质量门；审核后才会成为可供新告警使用的经验。`
                  : `${candidateKind}模式已经处理，无需再次审核。`}
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
                审核这条经验
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
          <p className="text-muted-foreground text-xs">Runtime Decision</p>
          <p className="mt-1 text-sm">
            Base {verdictLabel(alert.base_verdict)} → Effective{" "}
            {verdictLabel(alert.effective_verdict)}
          </p>
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

      <div className="grid border-b lg:grid-cols-4">
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
        <div className="border-b bg-sky-50 px-5 py-3 text-sm md:px-7">
          <span className="font-medium">历史处置依据：</span>
          {alert.operational_label_reason}
        </div>
      ) : null}

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

      {alert.memory_contexts.length ? (
        <div className="border-b px-5 py-4 md:px-7">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">本次使用的历史经验</h3>
            <Badge
              variant={
                memoryUsage.tone === "decision" ? "default" : "secondary"
              }
            >
              {memoryUsage.label}
            </Badge>
          </div>
          <p className="text-muted-foreground mt-2 text-xs">
            {memoryUsage.detail}
          </p>
          <div className="mt-3 divide-y border">
            {alert.memory_contexts.map((memory) => (
              <div key={memory.context_ref} className="px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <Badge variant="outline">{memory.context_ref}</Badge>
                    <span className="text-sm font-medium">{memory.label}</span>
                    {memory.applicability_status === "applicable" ? (
                      <Badge className="bg-emerald-700 text-white">
                        审核条件全部命中
                      </Badge>
                    ) : memory.use_mode === "context_only" ? (
                      <Badge
                        variant="outline"
                        className="border-sky-300 bg-sky-50 text-sky-800"
                      >
                        部分相似，仅供参考
                      </Badge>
                    ) : null}
                    {memory.reviewed_verdict ? (
                      <Badge
                        variant="outline"
                        className={verdictClass(memory.reviewed_verdict)}
                      >
                        历史审核：{verdictLabel(memory.reviewed_verdict)}
                      </Badge>
                    ) : null}
                    <span className="text-muted-foreground font-mono text-xs break-all">
                      {memory.source_id}
                    </span>
                  </div>
                  {alert.run_id && memory.source_id.startsWith("MEM-") ? (
                    <Button size="sm" variant="outline" asChild>
                      <Link
                        href={`/workspace/soc/memory/records/${encodeURIComponent(memory.source_id)}/revise?run_id=${encodeURIComponent(alert.run_id)}`}
                      >
                        <FilePenLineIcon className="size-4" />
                        纠正此 Memory
                      </Link>
                    </Button>
                  ) : null}
                </div>
                <p className="text-muted-foreground mt-2 text-sm leading-6">
                  {memory.summary}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {alert.decision_stages.length ? (
        <div className="px-5 py-4 md:px-7">
          <h3 className="text-sm font-semibold">
            决策来源与演变 / Decision Lineage
          </h3>
          <div className="mt-3 overflow-x-auto border">
            <table className="w-full min-w-[780px] text-left text-sm">
              <thead className="bg-zinc-50 text-xs">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Stage</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 font-medium">Verdict</th>
                  <th className="px-4 py-2.5 font-medium">Confidence</th>
                  <th className="px-4 py-2.5 font-medium">Summary</th>
                </tr>
              </thead>
              <tbody>
                {alert.decision_stages.map((stage) => (
                  <tr key={stage.stage} className="border-t">
                    <td className="px-4 py-3 font-mono text-xs">
                      {stage.stage}
                    </td>
                    <td className="px-4 py-3">{stage.status}</td>
                    <td className="px-4 py-3">{verdictLabel(stage.verdict)}</td>
                    <td className="px-4 py-3 tabular-nums">
                      {formatPercent(stage.confidence)}
                    </td>
                    <td className="px-4 py-3">{stage.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
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
  const query = useSocCorpusWorkbench();
  const activityQuery = useSocCorpusWorkbenchActivity();
  const processMutation = useProcessSocCorpusWorkbenchAlert();
  const state = query.state;
  const [search, setSearch] = useState("");
  const [readiness, setReadiness] = useState<ReadinessFilter>("all");
  const [comparison, setComparison] = useState<ComparisonFilter>("all");
  const [sourceType, setSourceType] = useState("all");
  const [groupId, setGroupId] = useState("all");
  const [unprocessedOnly, setUnprocessedOnly] = useState(true);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [runFeedbackByAlert, setRunFeedbackByAlert] = useState<
    Record<string, RunFeedback>
  >({});
  const [processingAlertIds, setProcessingAlertIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [filtersHydrated, setFiltersHydrated] = useState(false);
  const [page, setPage] = useState(0);
  const detailRef = useRef<HTMLDivElement>(null);
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
  const runFeedback = selectedAlertId
    ? (runFeedbackByAlert[selectedAlertId] ?? null)
    : null;
  const selectedAlertIsActive =
    !!selectedAlertId &&
    (processingAlertIds.has(selectedAlertId) ||
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

  useEffect(() => {
    if (!state) return;
    setRunFeedbackByAlert((current) => {
      let changed = false;
      const next = { ...current };
      for (const [alertId, feedback] of Object.entries(current)) {
        if (
          feedback.status !== "running" ||
          processingAlertIds.has(alertId) ||
          activeExecutionByAlert.has(alertId)
        ) {
          continue;
        }
        const alert = state.alerts.find((item) => item.alert_id === alertId);
        if (alert?.workflow_state === "completed") {
          next[alertId] = {
            alertId,
            status: "completed",
            message: "完整研判链路已完成，可按需查看详细结果。",
          };
          changed = true;
        } else if (alert?.workflow_state === "failed") {
          next[alertId] = {
            alertId,
            status: "failed",
            message: alert.failure_message ?? "告警处理失败",
          };
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [activeExecutionByAlert, processingAlertIds, state]);

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
      if (stored.selectedAlertId !== undefined) {
        setSelectedAlertId(stored.selectedAlertId);
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
      selectedAlertId,
    };
    try {
      window.sessionStorage.setItem(
        FILTER_STORAGE_KEY,
        JSON.stringify(snapshot),
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
    selectedAlertId,
    sourceType,
    unprocessedOnly,
  ]);

  const sourceTypes = useMemo(
    () =>
      Array.from(
        new Set(state?.alerts.map((item) => item.source_type) ?? []),
      ).sort(),
    [state?.alerts],
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

  const filtered = useMemo(() => {
    if (!state) return [];
    const needle = search.trim().toLocaleLowerCase();
    return state.alerts
      .filter((alert) => readiness === "all" || alert.readiness === readiness)
      .filter(
        (alert) => sourceType === "all" || alert.source_type === sourceType,
      )
      .filter((alert) => groupId === "all" || alert.group_id === groupId)
      .filter(
        (alert) =>
          !unprocessedOnly ||
          alert.workflow_state !== "completed" ||
          processingAlertIds.has(alert.alert_id) ||
          activeExecutionByAlert.has(alert.alert_id) ||
          alert.alert_id === selectedAlertId,
      )
      .filter((alert) => {
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
      })
      .sort((left, right) => left.sequence_number - right.sequence_number);
  }, [
    comparison,
    groupId,
    readiness,
    search,
    sourceType,
    state,
    unprocessedOnly,
    activeExecutionByAlert,
    processingAlertIds,
    selectedAlertId,
  ]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageAlerts = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [comparison, groupId, readiness, search, sourceType, unprocessedOnly]);

  useEffect(() => {
    if (!filtersHydrated) return;
    if (!filtered.length) {
      setSelectedAlertId(null);
      return;
    }
    if (filtered.some((item) => item.alert_id === selectedAlertId)) return;
    setSelectedAlertId(filtered[0]?.alert_id ?? null);
  }, [filtered, filtersHydrated, selectedAlertId]);

  const selectedAlert = useMemo(
    () =>
      state?.alerts.find((item) => item.alert_id === selectedAlertId) ?? null,
    [selectedAlertId, state?.alerts],
  );

  const handleProcess = async (alertId: string) => {
    if (
      processingAlertIds.has(alertId) ||
      activeExecutionByAlert.has(alertId)
    ) {
      toast.info(`Alert ${alertId} 已在运行，不会重复执行`);
      return;
    }
    setSelectedAlertId(alertId);
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
      },
    }));
    try {
      const result = await processMutation.mutateAsync(alertId);
      const updatedAlert = result.state.alerts.find(
        (item) => item.alert_id === alertId,
      );
      let feedbackMessage = "完整研判链路已完成，可按需查看详细结果。";
      let feedbackAction: RunFeedback["action"];
      if (result.execution_mode === "rerun") {
        feedbackMessage =
          "重新运行已完成；本次生成了新 Run，但不会重复累计同一告警的模式样本。";
        toast.success("重新运行完成", {
          description:
            "本次已创建新的 Runtime Run；同一原始告警不会重复增加 Pattern 支持数。",
          duration: 10_000,
        });
      } else if (
        updatedAlert?.candidate_id &&
        updatedAlert.candidate_status === "pending_review" &&
        !updatedAlert.memory_id
      ) {
        feedbackMessage = `“${updatedAlert.rule_name ?? "当前告警模式"}”已达到经验沉淀质量门，等待审核。`;
        feedbackAction = {
          href: `/workspace/soc/review/memory-candidates/${encodeURIComponent(updatedAlert.candidate_id)}`,
          label: "审核这条经验",
        };
        toast.success("同类经验待审核", {
          description: `已累计 ${updatedAlert.pattern_support_count ?? updatedAlert.window_alert_count} 条有效观察；可通过运行完成提示中的“审核这条经验”进入。`,
          duration: 12_000,
        });
      } else if (updatedAlert?.memory_id) {
        const memoryUse = memoryRunUsageCopy(
          updatedAlert.memory_contexts.length,
          updatedAlert.memory_directive_applied,
        );
        feedbackMessage = `本次已采用“${updatedAlert.rule_name ?? "当前告警模式"}”的审核经验：${memoryUse.label}。`;
        feedbackAction = {
          href: `/workspace/soc/memory/records/${encodeURIComponent(updatedAlert.memory_id)}`,
          label: "查看采用的经验",
        };
        toast.success("已采用审核经验", {
          description: `${memoryUse.label}：${memoryUse.detail}`,
          duration: 10_000,
        });
      } else {
        toast.success(
          result.idempotent
            ? `Alert ${alertId} 已存在，返回原结果`
            : `Alert ${alertId} 已完成 Runtime 与 Pattern 写入`,
        );
      }
      setRunFeedbackByAlert((current) => ({
        ...current,
        [alertId]: {
          alertId,
          status: "completed",
          message: feedbackMessage,
          action: feedbackAction,
        },
      }));
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
    setSearch("");
    setReadiness("all");
    setComparison("all");
    setSourceType(target.source_type);
    setGroupId(target.actual_group_id);
    setUnprocessedOnly(false);
    setSelectedAlertId(target.primary_alert_id);
    setPage(0);
  };

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
                activityQuery.activity?.active_count
                  ? "border-sky-300 bg-sky-50 text-sky-800"
                  : "border-zinc-300 bg-white text-zinc-700",
              )}
              title="不同告警可并行；同一告警只允许一个活动执行"
            >
              <ActivityIcon className="size-3.5" />
              运行中 {activityQuery.activity?.active_count ?? 0}/
              {activityQuery.activity?.max_concurrent_executions ?? 1}
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
            <span>
              企业专属策略 ·{" "}
              {formatSocDevPolicyLabel({
                tenantPolicy: state.safety.tenant_policy,
                softwarePathFastPolicy: state.safety.software_path_fast_policy,
              })}
            </span>
            <span>外部动作 · 关闭</span>
          </div>
          <span className="font-mono">
            {state.source.file_name} · {shortHash(state.source.sha256)}
          </span>
        </section>

        <SocLeadershipDemoGuidePanel
          guide={state.leadership_demo}
          groups={state.groups}
          alerts={state.alerts}
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
            <div className="min-w-64 flex-1">
              <label
                htmlFor="corpus-group-filter"
                className="mb-1.5 block text-xs font-medium"
              >
                行为模式组
              </label>
              <Select
                value={groupId}
                onValueChange={(value) => {
                  setGroupId(value);
                  if (value !== "all") setReadiness("all");
                }}
              >
                <SelectTrigger id="corpus-group-filter" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部行为模式组</SelectItem>
                  {state.groups
                    .filter((group) => group.alert_count >= 2)
                    .map((group) => (
                      <SelectItem key={group.group_id} value={group.group_id}>
                        {formatCorpusGroupOption(group)}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
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
                setUnprocessedOnly(true);
              }}
            >
              <RotateCcwIcon className="size-4" />
            </Button>
          </div>
          <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-2 text-xs">
            <FilterIcon className="size-3.5" />
            <span>{filtered.length} 条命中筛选</span>
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
                  <th className="w-36 px-4 py-2.5 font-medium">最终结论</th>
                  <th className="w-44 px-4 py-2.5 font-medium">
                    本次动作 / 历史标签
                  </th>
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
                  const localProcessing = processingAlertIds.has(
                    alert.alert_id,
                  );
                  const activeExecution =
                    activeExecutionByAlert.get(alert.alert_id) ??
                    alert.active_execution ??
                    null;
                  const processing =
                    localProcessing ||
                    !!activeExecution ||
                    alert.workflow_state === "running";
                  const otherSessionProcessing = !localProcessing && processing;
                  const executionCapacityFull =
                    activityQuery.activity?.available_slots === 0;
                  return (
                    <tr
                      key={alert.alert_id}
                      data-alert-id={alert.alert_id}
                      className={cn(
                        "cursor-pointer border-t align-top hover:bg-zinc-50",
                        verdictRowClass(alert.effective_verdict),
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
                        <div className="flex items-start gap-2">
                          {alert.effective_verdict === "false_positive" ? (
                            <ShieldCheckIcon className="mt-0.5 size-4 shrink-0 text-emerald-700" />
                          ) : alert.effective_verdict ? (
                            <AlertTriangleIcon
                              className={cn(
                                "mt-0.5 size-4 shrink-0",
                                alert.effective_verdict === "true_positive"
                                  ? "text-red-700"
                                  : "text-amber-700",
                              )}
                            />
                          ) : (
                            <ActivityIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
                          )}
                          <div className="min-w-0">
                            <Badge
                              variant="outline"
                              className={verdictClass(alert.effective_verdict)}
                            >
                              {verdictLabel(alert.effective_verdict)}
                            </Badge>
                            <p className="text-muted-foreground mt-1 text-xs">
                              Base {verdictLabel(alert.base_verdict)}
                            </p>
                          </div>
                        </div>
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
                {runFeedback.action ? (
                  <Button size="sm" asChild>
                    <Link href={runFeedback.action.href}>
                      <BrainCircuitIcon className="size-4" />
                      {runFeedback.action.label}
                    </Link>
                  </Button>
                ) : null}
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
