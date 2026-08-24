"use client";

import {
  AlertTriangleIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  Clock3Icon,
  DatabaseIcon,
  ExternalLinkIcon,
  FlaskConicalIcon,
  LockIcon,
  PlayIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useProcessSocMemoryWorkbenchAlert,
  useSocMemoryWorkbench,
} from "@/core/soc";
import type {
  SocMemoryWorkbenchAlert,
  SocMemoryWorkbenchPhase,
  SocMemoryWorkbenchState,
  SocVerdict,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const PHASE_LABELS: Record<SocMemoryWorkbenchPhase, string> = {
  construction: "模式构建",
  held_out: "留出验证",
  additional: "扩展复测",
};

const VERDICT_LABELS: Record<SocVerdict, string> = {
  true_positive: "真实风险",
  suspicious: "可疑",
  false_positive: "误报",
  unknown: "未知",
  needs_review: "需复核",
};

const NEXT_ACTION_LABELS: Record<
  SocMemoryWorkbenchState["progress"]["next_action"],
  string
> = {
  process_construction: "运行下一条模式构建告警",
  review_candidate: "审核 Pattern Candidate",
  enable_memory: "补齐 Business Lesson 与检索治理",
  process_held_out: "运行留出告警并验证 Memory",
  process_additional: "运行下一条扩展样本",
  quality_gate_blocked: "模式质量门未通过",
  complete: "本轮 14 条验证完成",
};

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
  if (value < 1_000) return `${value} ms`;
  return `${(value / 1_000).toFixed(1)} s`;
}

function formatPercent(value?: number | null) {
  if (typeof value !== "number") return "-";
  return `${Math.round(value * 100)}%`;
}

function shortHash(value: string) {
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

function stateLabel(alert: SocMemoryWorkbenchAlert) {
  return {
    locked: "等待前序",
    ready: "可运行",
    analysis_only: "待写 Pattern",
    completed: "已完成",
    failed: "运行失败",
  }[alert.workflow_state];
}

function stateIcon(alert: SocMemoryWorkbenchAlert) {
  if (alert.workflow_state === "completed") {
    return <CheckCircle2Icon className="size-4 text-emerald-600" />;
  }
  if (alert.workflow_state === "failed") {
    return <AlertTriangleIcon className="size-4 text-red-600" />;
  }
  if (alert.workflow_state === "ready") {
    return <PlayIcon className="size-4 text-sky-700" />;
  }
  if (alert.workflow_state === "analysis_only") {
    return <CircleDashedIcon className="size-4 text-amber-600" />;
  }
  return <LockIcon className="size-4 text-zinc-400" />;
}

function WorkflowSummary({ state }: { state: SocMemoryWorkbenchState }) {
  const cells = [
    {
      label: "1. Pattern Observation",
      value: `${state.progress.construction_processed}/${state.progress.construction_target}`,
      ready:
        state.progress.construction_processed ===
        state.progress.construction_target,
    },
    {
      label: "2. Candidate Review",
      value: state.progress.candidate_state,
      ready: !!state.candidate,
    },
    {
      label: "3. Confirmed Memory",
      value: state.progress.memory_state,
      ready: state.progress.memory_state === "decision_ready",
    },
    {
      label: "4. Held-out Decision",
      value: state.progress.held_out_processed ? "completed" : "pending",
      ready: state.progress.held_out_processed,
    },
  ];
  return (
    <section className="grid border-b sm:grid-cols-2 xl:grid-cols-4">
      {cells.map((cell) => (
        <div
          key={cell.label}
          className="min-w-0 border-r border-b px-5 py-4 last:border-r-0 xl:border-b-0 xl:[&:last-child]:border-r-0 sm:[&:nth-child(2n)]:border-r-0 xl:[&:nth-child(2n)]:border-r"
        >
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground truncate text-xs">
              {cell.label}
            </span>
            {cell.ready ? (
              <CheckCircle2Icon className="size-4 shrink-0 text-emerald-600" />
            ) : (
              <CircleDashedIcon className="size-4 shrink-0 text-zinc-400" />
            )}
          </div>
          <p className="mt-2 truncate text-sm font-medium">{cell.value}</p>
        </div>
      ))}
    </section>
  );
}

function CandidateBand({ state }: { state: SocMemoryWorkbenchState }) {
  const candidate = state.candidate;
  return (
    <section className="border-b px-5 py-4 md:px-7">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <BrainCircuitIcon className="size-4" />
            <h2 className="text-sm font-semibold">Pattern / Memory 治理</h2>
            <Badge variant="outline">
              {NEXT_ACTION_LABELS[state.progress.next_action]}
            </Badge>
          </div>
          <p className="text-muted-foreground mt-2 text-sm">
            {candidate
              ? `${candidate.candidate_id} · ${candidate.summary}`
              : `支持度 ${state.progress.construction_processed}/${state.progress.construction_target}，达到门槛后才生成候选。`}
          </p>
          {candidate ? (
            <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
              <span>状态 {candidate.status}</span>
              <span>一致率 {formatPercent(candidate.consistency_ratio)}</span>
              <span>Memory {candidate.memory_id ?? "未创建"}</span>
              <span>
                检索 {candidate.retrieval_enabled ? "enabled" : "disabled"}
              </span>
              <span>
                Decision Directive{" "}
                {candidate.decision_directive_ready ? "ready" : "missing"}
              </span>
            </div>
          ) : null}
        </div>
        {candidate ? (
          <Button size="sm" asChild>
            <Link
              href={`/workspace/soc/review/memory-candidates/${encodeURIComponent(candidate.candidate_id)}`}
            >
              <ShieldCheckIcon className="size-4" />
              审核并决定
              <ExternalLinkIcon className="size-3.5" />
            </Link>
          </Button>
        ) : null}
      </div>
    </section>
  );
}

function AlertTable({
  state,
  selectedAlertId,
  processingAlertId,
  onSelect,
  onProcess,
}: {
  state: SocMemoryWorkbenchState;
  selectedAlertId: string | null;
  processingAlertId: string | null;
  onSelect: (alertId: string) => void;
  onProcess: (alertId: string) => void;
}) {
  return (
    <section className="border-b">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 md:px-7">
        <div>
          <h2 className="text-sm font-semibold">GalaxyLab Cohort</h2>
          <p className="text-muted-foreground mt-0.5 text-xs">
            {state.cohort.rule_code} · {state.cohort.rule_name}
          </p>
        </div>
        <div className="text-muted-foreground flex items-center gap-2 text-xs">
          <span>{state.progress.processed_count}/14 processed</span>
          <Progress
            className="h-1.5 w-28"
            value={(state.progress.processed_count / 14) * 100}
          />
        </div>
      </div>
      <div className="overflow-x-auto border-t">
        <table className="w-full min-w-[1080px] table-fixed text-left text-sm">
          <thead className="bg-muted/40 text-muted-foreground text-xs">
            <tr>
              <th className="w-28 px-4 py-2.5 font-medium">阶段</th>
              <th className="w-28 px-4 py-2.5 font-medium">Alert ID</th>
              <th className="w-40 px-4 py-2.5 font-medium">事件时间</th>
              <th className="w-44 px-4 py-2.5 font-medium">终端</th>
              <th className="w-32 px-4 py-2.5 font-medium">状态</th>
              <th className="w-28 px-4 py-2.5 font-medium">Base</th>
              <th className="w-28 px-4 py-2.5 font-medium">Effective</th>
              <th className="w-28 px-4 py-2.5 font-medium">24h Pattern</th>
              <th className="w-32 px-4 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {state.alerts.map((alert) => {
              const processing = processingAlertId === alert.alert_id;
              return (
                <tr
                  key={alert.alert_id}
                  className={cn(
                    "hover:bg-muted/30 cursor-pointer border-t transition-colors",
                    selectedAlertId === alert.alert_id && "bg-sky-50/70",
                  )}
                  onClick={() => onSelect(alert.alert_id)}
                >
                  <td className="px-4 py-3">
                    <Badge variant="outline" className="font-normal">
                      {PHASE_LABELS[alert.phase]} {alert.phase_order}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {alert.alert_id}
                  </td>
                  <td className="px-4 py-3 tabular-nums">
                    {formatDateTime(alert.observed_at)}
                  </td>
                  <td className="px-4 py-3">
                    <p className="truncate">{alert.endpoint ?? "-"}</p>
                    <p className="text-muted-foreground mt-0.5 truncate text-xs">
                      {alert.host_name ?? "host unavailable"}
                    </p>
                  </td>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-2">
                      {stateIcon(alert)}
                      {stateLabel(alert)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <Badge
                      variant="outline"
                      className={cn(
                        "font-normal",
                        verdictClass(alert.base_verdict),
                      )}
                    >
                      {verdictLabel(alert.base_verdict)}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <Badge
                      variant="outline"
                      className={cn(
                        "font-normal",
                        verdictClass(alert.effective_verdict),
                      )}
                    >
                      {verdictLabel(alert.effective_verdict)}
                    </Badge>
                  </td>
                  <td
                    className="px-4 py-3 tabular-nums"
                    title="同一租户、环境、行为模式及 24 小时窗口内的观察数和独立来源数"
                  >
                    {typeof alert.pattern_support_count === "number" ? (
                      <span className="flex flex-col gap-0.5">
                        <span>{alert.pattern_support_count} 条</span>
                        <span className="text-muted-foreground text-xs">
                          {alert.pattern_distinct_source_count ?? 0} 来源
                        </span>
                      </span>
                    ) : (
                      "-"
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {alert.queue_id && !alert.can_process ? (
                      <Button variant="ghost" size="sm" asChild>
                        <Link
                          href={`/workspace/soc/review/alerts?queue_id=${encodeURIComponent(alert.queue_id)}`}
                          onClick={(event) => event.stopPropagation()}
                        >
                          <ExternalLinkIcon className="size-4" />
                          复核
                        </Link>
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant={alert.can_process ? "default" : "outline"}
                        disabled={!alert.can_process || processing}
                        onClick={(event) => {
                          event.stopPropagation();
                          onProcess(alert.alert_id);
                        }}
                      >
                        {processing ? (
                          <RefreshCwIcon className="size-4 animate-spin" />
                        ) : alert.workflow_state === "completed" ? (
                          <CheckCircle2Icon className="size-4" />
                        ) : (
                          <PlayIcon className="size-4" />
                        )}
                        {processing ? "研判中" : "运行"}
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AlertDetail({ alert }: { alert: SocMemoryWorkbenchAlert | null }) {
  if (!alert) return null;
  return (
    <section className="px-5 py-5 md:px-7">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">
            Alert {alert.alert_id} · {PHASE_LABELS[alert.phase]}
          </h2>
          <p className="text-muted-foreground mt-1 text-xs">
            {alert.run_id ?? "Runtime 尚未执行"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {alert.model_name ? (
            <Badge variant="outline">{alert.model_name}</Badge>
          ) : null}
          {alert.output_quality ? (
            <Badge variant="outline">quality: {alert.output_quality}</Badge>
          ) : null}
          <Badge variant="outline">
            <Clock3Icon className="size-3.5" />
            {formatDuration(alert.total_duration_ms)}
          </Badge>
        </div>
      </div>

      <div className="bg-border mt-5 grid gap-px border lg:grid-cols-3">
        <div className="bg-background px-4 py-3">
          <p className="text-muted-foreground text-xs">Endpoint / Process</p>
          <p className="mt-1 text-sm">{alert.endpoint ?? "-"}</p>
          <p className="text-muted-foreground mt-1 truncate text-xs">
            {alert.process_names.join(" → ") || "-"}
          </p>
        </div>
        <div className="bg-background px-4 py-3">
          <p className="text-muted-foreground text-xs">24h Pattern Quality</p>
          <p className="mt-1 text-sm">
            {alert.pattern_support_count ?? 0} 条观察 ·{" "}
            {alert.pattern_distinct_source_count ?? 0} 个独立来源
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            consistency {formatPercent(alert.pattern_consistency_ratio)} · gate{" "}
            {alert.pattern_quality_gate_passed === null ||
            alert.pattern_quality_gate_passed === undefined
              ? "-"
              : alert.pattern_quality_gate_passed
                ? "passed"
                : "withheld"}
          </p>
        </div>
        <div className="bg-background px-4 py-3">
          <p className="text-muted-foreground text-xs">Memory Context</p>
          <p className="mt-1 text-sm">
            {alert.memory_contexts.length} confirmed record(s)
          </p>
          <p className="text-muted-foreground mt-1 truncate text-xs">
            {alert.memory_contexts.map((item) => item.context_ref).join(", ") ||
              "No M-* context used"}
          </p>
        </div>
      </div>

      {alert.analysis_summary || alert.analysis_reason ? (
        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          <div>
            <h3 className="text-xs font-semibold tracking-normal text-zinc-500 uppercase">
              Analysis Summary
            </h3>
            <p className="mt-2 text-sm leading-6">
              {alert.analysis_summary ?? "-"}
            </p>
          </div>
          <div>
            <h3 className="text-xs font-semibold tracking-normal text-zinc-500 uppercase">
              Analysis Reason
            </h3>
            <p className="mt-2 text-sm leading-6">
              {alert.analysis_reason ?? "-"}
            </p>
          </div>
        </div>
      ) : null}

      {alert.decision_stages.length > 0 ? (
        <div className="mt-6">
          <h3 className="text-sm font-semibold">Decision Lineage</h3>
          <div className="bg-border mt-3 grid gap-px border md:grid-cols-2 xl:grid-cols-4">
            {alert.decision_stages.map((stage) => (
              <div
                key={stage.stage}
                className="bg-background min-w-0 px-4 py-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-medium tracking-normal uppercase">
                    {stage.stage.replace("_", " ")}
                  </p>
                  <Badge variant="outline" className="font-normal">
                    {stage.status}
                  </Badge>
                </div>
                <p className="mt-2 text-sm font-medium">
                  {verdictLabel(stage.verdict)} ·{" "}
                  {formatPercent(stage.confidence)}
                </p>
                <p className="text-muted-foreground mt-2 line-clamp-3 text-xs leading-5">
                  {stage.summary}
                </p>
                {stage.source_id ? (
                  <p className="text-muted-foreground mt-2 truncate font-mono text-[11px]">
                    {stage.source_id}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {alert.memory_contexts.length > 0 ? (
        <div className="mt-6">
          <h3 className="text-sm font-semibold">Retrieved Memory</h3>
          <div className="mt-3 divide-y border">
            {alert.memory_contexts.map((memory) => (
              <div key={memory.context_ref} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{memory.context_ref}</Badge>
                  <span className="text-sm font-medium">{memory.label}</span>
                </div>
                <p className="text-muted-foreground mt-2 text-sm leading-6">
                  {memory.summary}
                </p>
                <p className="text-muted-foreground mt-1 font-mono text-[11px]">
                  {memory.source_id}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function SocMemoryValidationWorkbench() {
  const query = useSocMemoryWorkbench();
  const processMutation = useProcessSocMemoryWorkbenchAlert();
  const state = query.state;
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);

  useEffect(() => {
    if (!state) return;
    if (
      selectedAlertId &&
      state.alerts.some((item) => item.alert_id === selectedAlertId)
    ) {
      return;
    }
    setSelectedAlertId(
      state.progress.next_alert_id ?? state.alerts[0]?.alert_id ?? null,
    );
  }, [selectedAlertId, state]);

  const selectedAlert = useMemo(
    () =>
      state?.alerts.find((item) => item.alert_id === selectedAlertId) ?? null,
    [selectedAlertId, state?.alerts],
  );

  const handleProcess = async (alertId: string) => {
    setSelectedAlertId(alertId);
    try {
      const result = await processMutation.mutateAsync(alertId);
      toast.success(
        result.idempotent
          ? `Alert ${alertId} 已存在，返回原运行结果`
          : `Alert ${alertId} 已完成 Runtime 与 Pattern 写入`,
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "告警处理失败");
    }
  };

  if (query.isLoading && !state) {
    return (
      <div className="flex size-full min-h-0 flex-col">
        <SocWorkspaceHeader
          icon={FlaskConicalIcon}
          title="DEV · GalaxyLab Memory 闭环"
          description="GalaxyLab T1003 固定样本的 5+1 治理生命周期"
        />
        <div className="space-y-4 p-6" aria-label="正在加载 Memory 验证工作台">
          <Skeleton className="h-32 w-full rounded-md" />
          <Skeleton className="h-96 w-full rounded-md" />
        </div>
      </div>
    );
  }

  if (query.error && !state) {
    return (
      <div className="flex size-full min-h-0 flex-col">
        <SocWorkspaceHeader
          icon={FlaskConicalIcon}
          title="DEV · GalaxyLab Memory 闭环"
          description="GalaxyLab T1003 固定样本的 5+1 治理生命周期"
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
                    : "后端未启用本地 Memory 工作台。"}
                </p>
              </div>
            </div>
          </div>
        </main>
      </div>
    );
  }

  if (!state) return null;
  const processingAlertId = processMutation.isPending
    ? (processMutation.variables ?? null)
    : null;

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={FlaskConicalIcon}
        title="DEV · GalaxyLab Memory 闭环"
        description="GalaxyLab T1003 固定样本的 5+1 治理生命周期"
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link href="/workspace/soc/memory">Memory Center</Link>
            </Button>
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
            <Button
              variant="outline"
              size="icon-sm"
              onClick={() => void query.refetch()}
              disabled={query.isFetching || processMutation.isPending}
              aria-label="刷新 Memory 验证状态"
              title="刷新 Memory 验证状态"
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
            <span>真实历史样本 · operational replay</span>
            <span>内部 Provider · off/mock</span>
            <span>Tenant Policy · disabled</span>
            <span>外部动作 · disabled</span>
          </div>
          <span className="font-mono">
            {state.source.file_name} · {shortHash(state.source.sha256)}
          </span>
        </section>

        <WorkflowSummary state={state} />

        <section className="border-b px-5 py-4 md:px-7">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold break-all">
              {state.cohort.detection_key}
            </h2>
            <div className="mt-2 flex min-w-0 flex-wrap gap-1.5">
              {state.cohort.behavior_components.map((component) => (
                <Badge
                  key={component}
                  variant="outline"
                  className="max-w-full shrink rounded-sm font-mono break-all whitespace-normal"
                >
                  {component}
                </Badge>
              ))}
            </div>
            <p className="text-muted-foreground mt-2 font-mono text-xs break-all">
              behavior {state.cohort.behavior_fingerprint}
            </p>
            <dl className="text-muted-foreground mt-3 grid min-w-0 gap-x-5 gap-y-2 border-t pt-3 text-xs sm:grid-cols-[minmax(0,1fr)_auto_auto]">
              <div className="flex min-w-0 items-baseline gap-1.5">
                <dt className="shrink-0 font-medium text-zinc-700">Model</dt>
                <dd
                  className="min-w-0 font-mono break-all"
                  title={state.model.model_name ?? "unresolved"}
                >
                  {state.model.model_name ?? "unresolved"}
                </dd>
              </div>
              <div className="flex items-baseline gap-1.5 whitespace-nowrap">
                <dt className="font-medium text-zinc-700">Thinking</dt>
                <dd>{state.model.thinking_enabled ? "on" : "off"}</dd>
              </div>
              <div className="flex items-baseline gap-1.5 whitespace-nowrap">
                <dt className="font-medium text-zinc-700">Role verifier</dt>
                <dd>{state.model.role_verifier_enabled ? "on" : "off"}</dd>
              </div>
            </dl>
          </div>
        </section>

        <CandidateBand state={state} />
        <AlertTable
          state={state}
          selectedAlertId={selectedAlertId}
          processingAlertId={processingAlertId}
          onSelect={setSelectedAlertId}
          onProcess={(alertId) => void handleProcess(alertId)}
        />
        <AlertDetail alert={selectedAlert} />
      </main>
    </div>
  );
}
