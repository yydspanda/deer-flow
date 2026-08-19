"use client";

import {
  AlertTriangleIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Clock3Icon,
  DatabaseIcon,
  ExternalLinkIcon,
  FileSearchIcon,
  FilterIcon,
  PlayIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchIcon,
  ShieldCheckIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useProcessSocCorpusWorkbenchAlert,
  useSocCorpusWorkbench,
} from "@/core/soc";
import type {
  SocCorpusWorkbenchAlert,
  SocCorpusWorkbenchReadiness,
  SocCorpusWorkbenchState,
  SocVerdict,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;

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
    label: "可构建候选",
    className: "border-emerald-300 bg-emerald-50 text-emerald-800",
    title:
      "强指纹且同一固定 24 小时窗口至少 5 条；仍需运行结果通过一致性质量门",
    rank: 0,
  },
  recurrent_strong: {
    label: "强指纹重复",
    className: "border-sky-300 bg-sky-50 text-sky-800",
    title: "长期同类告警至少 2 条，但当前 24 小时窗口不足 5 条",
    rank: 1,
  },
  singleton_strong: {
    label: "强指纹单例",
    className: "border-zinc-300 bg-zinc-50 text-zinc-700",
    title: "具备决策级行为指纹，但当前语料没有同类重复样本",
    rank: 2,
  },
  recurrent_context_only: {
    label: "上下文重复",
    className: "border-amber-300 bg-amber-50 text-amber-800",
    title: "存在重复模式，但指纹强度不足，只能作为上下文，不能承载决策指令",
    rank: 3,
  },
  context_only_singleton: {
    label: "弱信号单例",
    className: "border-zinc-300 bg-zinc-50 text-zinc-600",
    title: "只有弱行为信号且没有同类重复样本",
    rank: 4,
  },
  fingerprint_missing: {
    label: "无行为指纹",
    className: "border-red-300 bg-red-50 text-red-800",
    title: "可运行 Runtime，但不适合作为当前 Memory 泛化验证样本",
    rank: 5,
  },
};

type ReadinessFilter = SocCorpusWorkbenchReadiness | "all";

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

function SummaryBand({ state }: { state: SocCorpusWorkbenchState }) {
  const summary = state.readiness;
  const items = [
    {
      label: "Corpus",
      value: `${summary.total_alert_count} 条`,
      detail: `${summary.processed_count} 已完成 · ${summary.failed_count} 失败`,
    },
    {
      label: "Behavior Fingerprint",
      value: `${summary.fingerprint_coverage_count}/${summary.total_alert_count}`,
      detail: `${summary.decision_eligible_alert_count} 条决策级强指纹`,
    },
    {
      label: "Repeated Groups",
      value: `${summary.recurrent_group_count} 组`,
      detail: `${summary.recurrent_alert_count} 条属于重复组`,
    },
    {
      label: "24h Candidate Windows",
      value: `${summary.candidate_window_group_count} 组`,
      detail: `${summary.candidate_window_alert_count} 条可优先体验`,
    },
    {
      label: "Memory Hits",
      value: `${summary.memory_hit_alert_count} 条`,
      detail: "已在真实 Runtime 请求中投影 M-*",
    },
  ];
  return (
    <section className="grid border-b sm:grid-cols-2 xl:grid-cols-5">
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

function AlertDetail({ alert }: { alert: SocCorpusWorkbenchAlert | null }) {
  if (!alert) {
    return (
      <section className="text-muted-foreground px-5 py-10 text-center text-sm">
        选择一条告警查看运行结果
      </section>
    );
  }
  const readiness = READINESS[alert.readiness];
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
              <Badge className="bg-emerald-700">Memory 已应用</Badge>
            ) : null}
          </div>
          <p className="mt-1 text-sm">{alert.rule_name ?? "未命名规则"}</p>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">
            {alert.detection_key ?? "detection key unavailable"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {alert.queue_id ? (
            <Button variant="outline" size="sm" asChild>
              <Link
                href={`/workspace/soc/review/alerts?queue_id=${encodeURIComponent(alert.queue_id)}`}
              >
                <ShieldCheckIcon className="size-4" />
                复核告警
              </Link>
            </Button>
          ) : null}
          {alert.candidate_id ? (
            <Button variant="outline" size="sm" asChild>
              <Link
                href={`/workspace/soc/review/memory-candidates/${encodeURIComponent(alert.candidate_id)}`}
              >
                <BrainCircuitIcon className="size-4" />
                审核 Candidate
              </Link>
            </Button>
          ) : null}
        </div>
      </div>

      <div className="grid border-y bg-zinc-50 lg:grid-cols-4">
        <div className="border-r px-5 py-4">
          <p className="text-muted-foreground text-xs">Corpus Similarity</p>
          <p className="mt-1 text-sm font-medium">
            长期同类 {alert.group_alert_count} 条
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            当前 24h 窗口 {alert.window_alert_count} 条
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
          <p className="text-muted-foreground text-xs">Confirmed Memory</p>
          <p className="mt-1 text-sm">
            {alert.memory_contexts.length} 条命中 ·{" "}
            {alert.memory_directive_applied ? "指令已应用" : "仅上下文/未命中"}
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {alert.memory_effect ?? alert.memory_status ?? "-"}
          </p>
        </div>
      </div>

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
          <h3 className="text-sm font-semibold">本次召回的 Memory</h3>
          <div className="mt-3 divide-y border">
            {alert.memory_contexts.map((memory) => (
              <div key={memory.context_ref} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{memory.context_ref}</Badge>
                  <span className="text-sm font-medium">{memory.label}</span>
                  <span className="text-muted-foreground font-mono text-xs">
                    {memory.source_id}
                  </span>
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
          <h3 className="text-sm font-semibold">Decision Lineage</h3>
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
    </section>
  );
}

export function SocCorpusValidationWorkbench() {
  const query = useSocCorpusWorkbench();
  const processMutation = useProcessSocCorpusWorkbenchAlert();
  const state = query.state;
  const [search, setSearch] = useState("");
  const [readiness, setReadiness] =
    useState<ReadinessFilter>("candidate_window");
  const [sourceType, setSourceType] = useState("all");
  const [groupId, setGroupId] = useState("all");
  const [unprocessedOnly, setUnprocessedOnly] = useState(true);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const sourceTypes = useMemo(
    () =>
      Array.from(
        new Set(state?.alerts.map((item) => item.source_type) ?? []),
      ).sort(),
    [state?.alerts],
  );

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
        (alert) => !unprocessedOnly || alert.workflow_state !== "completed",
      )
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
      .sort((left, right) => {
        const rank =
          READINESS[left.readiness].rank - READINESS[right.readiness].rank;
        if (rank !== 0) return rank;
        if (left.group_id !== right.group_id) {
          return left.group_id.localeCompare(right.group_id);
        }
        return left.observed_at.localeCompare(right.observed_at);
      });
  }, [groupId, readiness, search, sourceType, state, unprocessedOnly]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageAlerts = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [groupId, readiness, search, sourceType, unprocessedOnly]);

  useEffect(() => {
    if (!filtered.length) {
      setSelectedAlertId(null);
      return;
    }
    if (filtered.some((item) => item.alert_id === selectedAlertId)) return;
    setSelectedAlertId(filtered[0]?.alert_id ?? null);
  }, [filtered, selectedAlertId]);

  const selectedAlert = useMemo(
    () =>
      state?.alerts.find((item) => item.alert_id === selectedAlertId) ?? null,
    [selectedAlertId, state?.alerts],
  );

  const handleProcess = async (alertId: string) => {
    setSelectedAlertId(alertId);
    try {
      const result = await processMutation.mutateAsync(alertId);
      setUnprocessedOnly(false);
      toast.success(
        result.idempotent
          ? `Alert ${alertId} 已存在，返回原结果`
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
          icon={FileSearchIcon}
          title="SOC 语料验证"
          description="全量历史样本的单告警 Runtime 与 Memory 实验"
        />
        <div className="space-y-4 p-6" aria-label="正在加载 SOC 语料工作台">
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
          title="SOC 语料验证"
          description="全量历史样本的单告警 Runtime 与 Memory 实验"
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
                    : "后端未启用本地语料工作台。"}
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
        icon={FileSearchIcon}
        title="SOC 语料验证"
        description="全量历史样本的单告警 Runtime 与 Memory 实验"
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
            <Button
              variant="outline"
              size="icon-sm"
              onClick={() => void query.refetch()}
              disabled={query.isFetching || processMutation.isPending}
              aria-label="刷新语料验证状态"
              title="刷新语料验证状态"
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
            <span>历史 PKL · operational replay</span>
            <span>内部 Provider · off/mock</span>
            <span>Tenant Policy · disabled</span>
            <span>外部动作 · disabled</span>
          </div>
          <span className="font-mono">
            {state.source.file_name} · {shortHash(state.source.sha256)}
          </span>
        </section>

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
                  placeholder="Alert ID / Rule / Host / IP"
                  className="pl-9"
                />
              </div>
            </div>
            <div className="w-44">
              <label className="mb-1.5 block text-xs font-medium">
                适配层级
              </label>
              <Select
                value={readiness}
                onValueChange={(value) =>
                  setReadiness(value as ReadinessFilter)
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部层级</SelectItem>
                  {Object.entries(READINESS).map(([value, item]) => (
                    <SelectItem key={value} value={value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="w-36">
              <label className="mb-1.5 block text-xs font-medium">来源</label>
              <Select value={sourceType} onValueChange={setSourceType}>
                <SelectTrigger className="w-full">
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
            <div className="min-w-64 flex-1">
              <label className="mb-1.5 block text-xs font-medium">同类组</label>
              <Select
                value={groupId}
                onValueChange={(value) => {
                  setGroupId(value);
                  if (value !== "all") setReadiness("all");
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部同类组</SelectItem>
                  {state.groups
                    .filter((group) => group.alert_count >= 2)
                    .map((group) => (
                      <SelectItem key={group.group_id} value={group.group_id}>
                        {group.rule_name ??
                          group.detection_key ??
                          group.group_id}{" "}
                        · {group.alert_count}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <label className="flex h-9 items-center gap-2 border px-3 text-sm">
              <Switch
                checked={unprocessedOnly}
                onCheckedChange={setUnprocessedOnly}
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
                setReadiness("candidate_window");
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
            <span>结构适配不代表研判准确率或候选质量门已经通过</span>
          </div>
        </section>

        <section className="border-b">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1180px] table-fixed text-left text-sm">
              <thead className="bg-zinc-50 text-xs">
                <tr>
                  <th className="w-28 px-4 py-2.5 font-medium">Alert</th>
                  <th className="w-36 px-4 py-2.5 font-medium">时间 / 来源</th>
                  <th className="w-72 px-4 py-2.5 font-medium">规则</th>
                  <th className="w-40 px-4 py-2.5 font-medium">结构适配</th>
                  <th className="w-36 px-4 py-2.5 font-medium">Decision</th>
                  <th className="w-32 px-4 py-2.5 font-medium">Pattern</th>
                  <th className="w-32 px-4 py-2.5 font-medium">Memory</th>
                  <th className="w-24 px-4 py-2.5 text-right font-medium">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {pageAlerts.map((alert) => {
                  const readinessItem = READINESS[alert.readiness];
                  const processing = processingAlertId === alert.alert_id;
                  return (
                    <tr
                      key={alert.alert_id}
                      className={cn(
                        "cursor-pointer border-t align-top hover:bg-zinc-50",
                        selectedAlertId === alert.alert_id && "bg-sky-50/70",
                      )}
                      onClick={() => setSelectedAlertId(alert.alert_id)}
                    >
                      <td className="px-4 py-3 font-mono text-xs">
                        {alert.alert_id}
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
                          同类 {alert.group_alert_count} · 24h{" "}
                          {alert.window_alert_count}
                        </p>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-1">
                          <Badge
                            variant="outline"
                            className={verdictClass(alert.base_verdict)}
                          >
                            {verdictLabel(alert.base_verdict)}
                          </Badge>
                          <span className="text-muted-foreground">→</span>
                          <Badge
                            variant="outline"
                            className={verdictClass(alert.effective_verdict)}
                          >
                            {verdictLabel(alert.effective_verdict)}
                          </Badge>
                        </div>
                        <p className="text-muted-foreground mt-1 text-xs">
                          {alert.workflow_state}
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
                        <p>
                          {alert.memory_contexts.length
                            ? `命中 ${alert.memory_contexts.length}`
                            : "-"}
                        </p>
                        {alert.memory_directive_applied ? (
                          <p className="mt-1 text-xs text-emerald-700">
                            指令已应用
                          </p>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          size="sm"
                          variant={
                            alert.workflow_state === "completed"
                              ? "ghost"
                              : "outline"
                          }
                          disabled={
                            !alert.can_process || processMutation.isPending
                          }
                          onClick={(event) => {
                            event.stopPropagation();
                            void handleProcess(alert.alert_id);
                          }}
                        >
                          {processing ? (
                            <RefreshCwIcon className="size-4 animate-spin" />
                          ) : alert.workflow_state === "failed" ? (
                            <RotateCcwIcon className="size-4" />
                          ) : alert.workflow_state === "completed" ? (
                            <CheckCircle2Icon className="size-4" />
                          ) : (
                            <PlayIcon className="size-4" />
                          )}
                          {alert.workflow_state === "completed"
                            ? "已完成"
                            : alert.workflow_state === "failed"
                              ? "重试"
                              : "运行"}
                        </Button>
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

        <AlertDetail alert={selectedAlert} />

        <section className="text-muted-foreground flex flex-wrap items-center gap-x-5 gap-y-2 border-t bg-zinc-50 px-5 py-3 text-xs md:px-7">
          <span className="flex items-center gap-1.5">
            <Clock3Icon className="size-3.5" />
            固定 Pattern 窗口 24h
          </span>
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
