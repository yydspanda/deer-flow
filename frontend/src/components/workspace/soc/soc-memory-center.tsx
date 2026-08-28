"use client";

import {
  ArchiveIcon,
  BookOpenIcon,
  BrainCircuitIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CircleDashedIcon,
  CirclePauseIcon,
  Clock3Icon,
  DatabaseIcon,
  HistoryIcon,
  Layers3Icon,
  RefreshCwIcon,
  RouteIcon,
  SearchIcon,
  ShieldCheckIcon,
  TargetIcon,
  TriangleAlertIcon,
  WorkflowIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
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
import { Switch } from "@/components/ui/switch";
import {
  memoryFutureUseCopy,
  memoryFutureUseStateCopy,
  MEMORY_MATCHING_RULE_STATE_LABELS,
  MEMORY_PATTERN_STAGE_DETAILS,
  MEMORY_PATTERN_STAGE_LABELS,
} from "@/components/workspace/soc/soc-memory-copy";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useSocMemoryCenterOverview,
  useSocMemoryCenterPattern,
  useSupersedeSocMemoryCandidate,
} from "@/core/soc";
import type {
  SocMemoryCenterPatternSummary,
  SocMemoryFutureUseState,
  SocMemoryPatternLifecycleState,
  SocMemoryPatternStageFilter,
  SocMemoryProfileState,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;
const OBSERVATION_PAGE_SIZE = 20;

const ATTENTION_REASON_LABELS: Record<string, string> = {
  unregistered_memory_profile: "匹配规则不可用",
  legacy_memory_profile: "匹配规则版本待升级",
  legacy_candidate_requires_reconciliation: "旧规则生成的候选需要处理",
  legacy_memory_requires_revalidation: "旧规则生成的经验需要重新校验",
  candidate_review_required: "存在待专家审核的经验候选",
  superseded_history: "该候选已被新版本替代",
  memory_retrieval_disabled: "已确认经验当前暂停用于新告警",
};

function formatTime(value?: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function shortId(value: string) {
  return value.length > 18
    ? `${value.slice(0, 10)}...${value.slice(-6)}`
    : value;
}

function Metric({
  label,
  value,
  tone = "neutral",
  className,
}: {
  label: string;
  value: number;
  tone?: "neutral" | "attention" | "positive";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "min-w-0 border-r border-b px-4 py-3 lg:border-b-0 lg:last:border-r-0",
        tone === "attention" &&
          value > 0 &&
          "bg-amber-50 text-amber-950 dark:bg-amber-950/30 dark:text-amber-100",
        tone === "positive" &&
          value > 0 &&
          "bg-emerald-50 text-emerald-950 dark:bg-emerald-950/30 dark:text-emerald-100",
        className,
      )}
    >
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function MatchingRuleBadge({ state }: { state: SocMemoryProfileState }) {
  return (
    <Badge
      variant={state === "current" ? "secondary" : "outline"}
      className={cn(
        state === "legacy" && "border-amber-400 text-amber-700",
        state === "unregistered" && "border-red-400 text-red-700",
      )}
    >
      {MEMORY_MATCHING_RULE_STATE_LABELS[state]}
    </Badge>
  );
}

function LifecycleBadge({
  state,
  compact = false,
}: {
  state: SocMemoryPatternLifecycleState;
  compact?: boolean;
}) {
  const Icon =
    state === "collecting"
      ? CircleDashedIcon
      : state === "candidate_pending"
        ? Clock3Icon
        : state === "candidate_intermediate"
          ? WorkflowIcon
          : state === "terminal_history"
            ? ArchiveIcon
            : DatabaseIcon;
  return (
    <Badge
      variant={state.startsWith("memory_") ? "secondary" : "outline"}
      className={cn(
        "max-w-full gap-1 text-left leading-4 whitespace-nowrap",
        compact && "px-1.5 py-0 text-[11px] 2xl:px-2 2xl:py-0.5 2xl:text-xs",
        state === "candidate_pending" && "border-amber-400 text-amber-700",
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      {MEMORY_PATTERN_STAGE_LABELS[state]}
    </Badge>
  );
}

function patternFutureUse(pattern: SocMemoryCenterPatternSummary) {
  if (pattern.future_use_state) {
    return memoryFutureUseStateCopy(pattern.future_use_state);
  }
  const record = pattern.memory_record;
  return memoryFutureUseCopy({
    hasRecord: record !== null && record !== undefined,
    retrievalEnabled: record?.retrieval_enabled ?? false,
    decisionDirectiveReady: record?.decision_directive_ready ?? false,
    matchingRuleState: pattern.profile_state,
  });
}

function FutureUseBadge({
  pattern,
  compact = false,
}: {
  pattern: SocMemoryCenterPatternSummary;
  compact?: boolean;
}) {
  const copy = patternFutureUse(pattern);
  const Icon =
    copy.tone === "decision"
      ? TargetIcon
      : copy.tone === "reference"
        ? BookOpenIcon
        : copy.tone === "paused"
          ? CirclePauseIcon
          : copy.tone === "blocked"
            ? TriangleAlertIcon
            : CircleDashedIcon;
  return (
    <Badge
      variant={copy.tone === "decision" ? "default" : "outline"}
      className={cn(
        "max-w-full gap-1 text-left leading-4 whitespace-nowrap",
        compact && "px-1.5 py-0 text-[11px] 2xl:px-2 2xl:py-0.5 2xl:text-xs",
        copy.tone === "reference" && "border-sky-400 text-sky-700",
        copy.tone === "paused" && "text-muted-foreground",
        copy.tone === "blocked" && "border-amber-400 text-amber-700",
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      {copy.label}
    </Badge>
  );
}

function MemoryGovernanceStatus({
  pattern,
}: {
  pattern: SocMemoryCenterPatternSummary;
}) {
  const record = pattern.memory_record;
  const copy = patternFutureUse(pattern);
  const governanceId =
    pattern.memory_record?.memory_id ?? pattern.candidate?.candidate_id;
  const LifecycleIcon =
    pattern.lifecycle_state === "collecting"
      ? CircleDashedIcon
      : pattern.lifecycle_state === "candidate_pending"
        ? Clock3Icon
        : pattern.lifecycle_state === "candidate_intermediate"
          ? WorkflowIcon
          : pattern.lifecycle_state === "terminal_history"
            ? ArchiveIcon
            : DatabaseIcon;
  const FutureUseIcon =
    copy.tone === "decision"
      ? TargetIcon
      : copy.tone === "reference"
        ? BookOpenIcon
        : copy.tone === "paused"
          ? CirclePauseIcon
          : copy.tone === "blocked"
            ? TriangleAlertIcon
            : CircleDashedIcon;
  return (
    <div className="grid border-b sm:grid-cols-2">
      <div className="border-b px-5 py-4 sm:border-r sm:border-b-0">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <LifecycleIcon className="size-4" />
          沉淀进度
        </h3>
        <p className="mt-2 text-sm font-medium">
          {MEMORY_PATTERN_STAGE_LABELS[pattern.lifecycle_state]}
        </p>
        <p className="text-muted-foreground mt-1 text-xs leading-5">
          {MEMORY_PATTERN_STAGE_DETAILS[pattern.lifecycle_state]}
        </p>
        {governanceId ? (
          <p className="text-muted-foreground mt-2 font-mono text-[11px]">
            {governanceId}
          </p>
        ) : null}
      </div>
      <div className="px-5 py-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <FutureUseIcon
            className={cn(
              "size-4",
              copy.tone === "decision" && "text-emerald-700",
            )}
          />
          新告警使用
        </h3>
        <p className="mt-2 text-sm font-medium">{copy.label}</p>
        <p className="text-muted-foreground mt-1 text-xs leading-5">
          {copy.detail}
        </p>
        {record ? (
          <p className="text-muted-foreground mt-2 font-mono text-[11px]">
            {record.memory_id}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function patternTitle(pattern: SocMemoryCenterPatternSummary) {
  return (
    pattern.pattern_label ?? pattern.candidate?.summary ?? pattern.pattern_value
  );
}

export function SocMemoryCenter({
  initialLineageKey,
}: {
  initialLineageKey?: string;
}) {
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [dataClass, setDataClass] = useState<
    "all" | "simulation" | "operational"
  >("all");
  const [stageFilter, setStageFilter] = useState<
    "all" | SocMemoryPatternStageFilter
  >("all");
  const [futureUseFilter, setFutureUseFilter] = useState<
    "all" | SocMemoryFutureUseState
  >("all");
  const [showHistory, setShowHistory] = useState(false);
  const [offset, setOffset] = useState(0);
  const [observationCursor, setObservationCursor] = useState<{
    lineageKey: string | null;
    offset: number;
    visible: boolean;
  }>({
    lineageKey: initialLineageKey ?? null,
    offset: 0,
    visible: false,
  });
  const { overview, isLoading, isFetching, error, refetch } =
    useSocMemoryCenterOverview({
      search,
      dataClass: dataClass === "all" ? null : dataClass,
      includeTerminalHistory: showHistory,
      stage: stageFilter === "all" ? null : stageFilter,
      futureUse: futureUseFilter === "all" ? null : futureUseFilter,
      limit: PAGE_SIZE,
      offset,
    });
  const selectedLineageKey = initialLineageKey ?? null;
  const observationOffset =
    observationCursor.lineageKey === selectedLineageKey
      ? observationCursor.offset
      : 0;
  const observationsVisible =
    observationCursor.lineageKey === selectedLineageKey &&
    observationCursor.visible;
  const {
    detail,
    isLoading: detailLoading,
    isFetching: detailFetching,
    error: detailError,
  } = useSocMemoryCenterPattern(selectedLineageKey, {
    includeObservations: observationsVisible,
    observationLimit: OBSERVATION_PAGE_SIZE,
    observationOffset,
  });
  const supersedeMutation = useSupersedeSocMemoryCandidate();

  useEffect(() => {
    setOffset(0);
  }, [search, dataClass, stageFilter, futureUseFilter, showHistory]);

  const activeFilterCount =
    Number(Boolean(search)) +
    Number(dataClass !== "all") +
    Number(stageFilter !== "all") +
    Number(futureUseFilter !== "all") +
    Number(showHistory);
  const resultStart = (overview?.total ?? 0) > 0 ? offset + 1 : 0;

  const resetFilters = () => {
    setSearchDraft("");
    setSearch("");
    setDataClass("all");
    setStageFilter("all");
    setFutureUseFilter("all");
    setShowHistory(false);
  };

  const handleSupersede = async () => {
    const candidate = detail?.candidates.find(
      (item) => item.candidate_id === detail.pattern.candidate?.candidate_id,
    );
    const successorId = detail?.suggested_successor_candidate_id;
    if (!candidate || !successorId) return;
    try {
      await supersedeMutation.mutateAsync({
        candidateId: candidate.candidate_id,
        request: {
          successor_candidate_id: successorId,
          reason:
            "Memory Center operator reconciled a same-alert candidate created by an older matching-rule contract.",
        },
      });
      toast.success("旧匹配规则候选已标记为历史替代项");
    } catch (cause) {
      toast.error(
        cause instanceof Error ? cause.message : "匹配规则候选处理失败",
      );
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={BrainCircuitIcon}
        title="SOC 经验中心"
        description="查看重复告警如何沉淀为经验，以及已确认经验是否开放给新告警使用"
        actions={
          <>
            <Button size="sm" variant="outline" asChild>
              <Link href="/workspace/soc/memory/records">
                <DatabaseIcon className="size-4" />
                经验台账
              </Link>
            </Button>
            <Button size="sm" asChild>
              <Link href="/workspace/soc/review/memory-candidates">
                <ShieldCheckIcon className="size-4" />
                待审核经验
                {(overview?.metrics.pending_candidate_count ?? 0) > 0 ? (
                  <span className="bg-primary-foreground/15 min-w-5 px-1.5 text-center text-xs tabular-nums">
                    {overview?.metrics.pending_candidate_count}
                  </span>
                ) : null}
              </Link>
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => void refetch()}
              disabled={isFetching}
              title="刷新经验中心"
              aria-label="刷新经验中心"
            >
              <RefreshCwIcon
                className={cn("size-4", isFetching && "animate-spin")}
              />
            </Button>
          </>
        }
      />

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full max-w-none flex-col gap-4 p-4 md:p-6 2xl:px-8">
          <section className="overflow-hidden border" aria-label="经验中心统计">
            <div className="grid grid-cols-2 lg:grid-cols-5">
              <Metric
                label="同类行为"
                value={overview?.metrics.pattern_count ?? 0}
              />
              <Metric
                label="告警样本"
                value={overview?.metrics.observation_count ?? 0}
              />
              <Metric
                label="待审候选"
                value={overview?.metrics.pending_candidate_count ?? 0}
                tone="attention"
              />
              <Metric
                label="已确认经验"
                value={overview?.metrics.confirmed_memory_count ?? 0}
                tone="positive"
              />
              <Metric
                label="已开放使用"
                value={overview?.metrics.retrieval_enabled_memory_count ?? 0}
                className="col-span-2 lg:col-span-1"
              />
            </div>
            <div className="text-muted-foreground flex flex-wrap gap-x-5 gap-y-1 border-t px-4 py-2 text-xs tabular-nums">
              <span>
                聚合时间窗 {overview?.metrics.aggregation_window_count ?? 0}
              </span>
              <span>
                已替代候选 {overview?.metrics.superseded_candidate_count ?? 0}
              </span>
              <span>
                匹配规则待升级{" "}
                {overview?.metrics.legacy_profile_pattern_count ?? 0}
              </span>
              <span>
                匹配规则不可用{" "}
                {overview?.metrics.unregistered_profile_pattern_count ?? 0}
              </span>
            </div>
          </section>

          <section className="border" aria-label="同类行为筛选">
            <div className="grid gap-3 p-3 md:grid-cols-2 xl:grid-cols-[minmax(16rem,1fr)_10rem_12rem_14rem]">
              <div className="space-y-1.5 md:col-span-2 xl:col-span-1">
                <div className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
                  <SearchIcon className="size-3.5" />
                  搜索
                </div>
                <form
                  className="flex min-w-0 gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    setSearch(searchDraft.trim());
                  }}
                >
                  <Input
                    value={searchDraft}
                    onChange={(event) => setSearchDraft(event.target.value)}
                    placeholder="同类行为、告警 ID 或经验内容"
                    aria-label="搜索同类行为"
                  />
                  <Button
                    type="submit"
                    variant="outline"
                    size="icon"
                    title="搜索"
                    aria-label="执行搜索"
                  >
                    <SearchIcon className="size-4" />
                  </Button>
                </form>
              </div>
              <div className="space-y-1.5">
                <div className="text-muted-foreground text-xs font-medium">
                  数据范围
                </div>
                <Select
                  value={dataClass}
                  onValueChange={(value) =>
                    setDataClass(value as "all" | "simulation" | "operational")
                  }
                >
                  <SelectTrigger className="w-full" aria-label="数据范围">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部数据</SelectItem>
                    <SelectItem value="operational">运营数据</SelectItem>
                    <SelectItem value="simulation">验证数据</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <div className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
                  <Layers3Icon className="size-3.5" />
                  沉淀阶段
                </div>
                <Select
                  value={stageFilter}
                  onValueChange={(value) => {
                    const next = value as "all" | SocMemoryPatternStageFilter;
                    setStageFilter(next);
                    if (next === "terminal") setShowHistory(true);
                  }}
                >
                  <SelectTrigger className="w-full" aria-label="按沉淀阶段筛选">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部阶段</SelectItem>
                    <SelectItem value="collecting">积累同类样本</SelectItem>
                    <SelectItem value="awaiting_review">
                      等待专家审核
                    </SelectItem>
                    <SelectItem value="materializing">正在生成经验</SelectItem>
                    <SelectItem value="persisted">经验已沉淀</SelectItem>
                    <SelectItem value="terminal">已结束</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <div className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
                  <RouteIcon className="size-3.5" />
                  新告警使用
                </div>
                <Select
                  value={futureUseFilter}
                  onValueChange={(value) =>
                    setFutureUseFilter(value as "all" | SocMemoryFutureUseState)
                  }
                >
                  <SelectTrigger
                    className="w-full"
                    aria-label="按新告警使用方式筛选"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部使用方式</SelectItem>
                    <SelectItem value="not_ready">尚未形成经验</SelectItem>
                    <SelectItem value="paused">尚未开放</SelectItem>
                    <SelectItem value="reference_only">仅供研判参考</SelectItem>
                    <SelectItem value="exact_match_decision">
                      精确匹配可复用结论
                    </SelectItem>
                    <SelectItem value="blocked">匹配规则待处理</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-3 border-t px-3 py-2">
              {(overview?.terminal_history_count ?? 0) > 0 ? (
                <label
                  htmlFor="memory-center-history"
                  className="flex cursor-pointer items-center gap-2 text-xs"
                >
                  <Switch
                    id="memory-center-history"
                    checked={showHistory}
                    onCheckedChange={(next) => {
                      setShowHistory(next);
                      if (!next && stageFilter === "terminal") {
                        setStageFilter("all");
                      }
                    }}
                  />
                  包含已结束同类行为 ({overview?.terminal_history_count ?? 0})
                </label>
              ) : null}
              {activeFilterCount > 0 ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={resetFilters}
                >
                  <XIcon className="size-4" />
                  清空筛选 ({activeFilterCount})
                </Button>
              ) : null}
              <div className="text-muted-foreground ml-auto flex flex-wrap justify-end gap-x-3 gap-y-1 text-xs">
                <span>最近样本优先</span>
                <span>{overview ? `${overview.total} 组同类行为` : "-"}</span>
              </div>
            </div>
          </section>

          <section
            className="grid min-h-[34rem] border xl:grid-cols-[minmax(36rem,0.95fr)_minmax(38rem,1.05fr)]"
            data-testid="memory-center-layout"
          >
            <div className="min-w-0 border-b xl:border-r xl:border-b-0">
              <div className="bg-muted/30 flex items-center justify-between gap-3 border-b px-4 py-2 text-xs font-medium">
                <span>同类行为</span>
                <span className="text-muted-foreground font-normal">
                  状态与样本
                </span>
              </div>
              {isLoading ? (
                <div className="text-muted-foreground flex h-48 items-center justify-center text-sm">
                  正在读取经验中心...
                </div>
              ) : error ? (
                <div className="text-destructive flex h-48 items-center justify-center px-6 text-center text-sm">
                  {error instanceof Error ? error.message : "经验中心加载失败"}
                </div>
              ) : (overview?.items.length ?? 0) === 0 ? (
                <div className="text-muted-foreground flex h-48 items-center justify-center text-sm">
                  当前筛选下没有同类行为。
                </div>
              ) : (
                <div className="divide-y">
                  {overview?.items.map((pattern) => (
                    <Link
                      key={pattern.lineage_key}
                      href={`/workspace/soc/memory/patterns/${pattern.lineage_key}`}
                      className={cn(
                        "hover:bg-muted/40 flex min-h-16 min-w-0 items-center gap-2 px-4 py-3 text-sm 2xl:gap-3",
                        selectedLineageKey === pattern.lineage_key &&
                          "bg-muted/60",
                      )}
                    >
                      <div
                        className="min-w-0 flex-1 truncate font-medium"
                        title={patternTitle(pattern)}
                        data-testid="memory-pattern-title"
                      >
                        {patternTitle(pattern)}
                      </div>
                      <div className="text-muted-foreground hidden shrink-0 text-xs tabular-nums 2xl:block">
                        {formatTime(pattern.last_observed_at)}
                      </div>
                      <div
                        className="flex shrink-0 flex-nowrap items-center gap-1.5"
                        data-testid="memory-pattern-statuses"
                      >
                        <LifecycleBadge
                          state={pattern.lifecycle_state}
                          compact
                        />
                        <FutureUseBadge pattern={pattern} compact />
                      </div>
                      <div
                        className="w-9 shrink-0 text-right text-xs font-medium whitespace-nowrap tabular-nums 2xl:w-auto"
                        title={`${pattern.support_count} 条告警，${pattern.aggregation_window_count} 个时间窗，最近样本 ${formatTime(pattern.last_observed_at)}`}
                        data-testid="memory-pattern-count"
                      >
                        {pattern.support_count} 条
                        <span className="text-muted-foreground ml-1 hidden font-normal 2xl:inline">
                          · {pattern.aggregation_window_count} 个时间窗
                        </span>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
              <div className="flex items-center justify-between border-t px-3 py-2">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  <ChevronLeftIcon className="size-4" />
                  上一页
                </Button>
                <span className="text-muted-foreground text-xs tabular-nums">
                  {resultStart}-
                  {Math.min(offset + PAGE_SIZE, overview?.total ?? 0)}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={offset + PAGE_SIZE >= (overview?.total ?? 0)}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  下一页
                  <ChevronRightIcon className="size-4" />
                </Button>
              </div>
            </div>

            <div className="min-w-0">
              {detailLoading ? (
                <div className="text-muted-foreground flex h-64 items-center justify-center text-sm">
                  正在加载同类行为详情...
                </div>
              ) : detailError ? (
                <div className="text-destructive flex h-64 items-center justify-center px-6 text-center text-sm">
                  {detailError instanceof Error
                    ? detailError.message
                    : "同类行为详情加载失败"}
                </div>
              ) : !detail ? (
                <div className="text-muted-foreground flex h-64 items-center justify-center text-sm">
                  选择一组同类行为查看详情。
                </div>
              ) : (
                <div className="flex flex-col">
                  <div className="border-b px-5 py-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h2 className="text-base font-semibold break-words">
                          {patternTitle(detail.pattern)}
                        </h2>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <LifecycleBadge
                            state={detail.pattern.lifecycle_state}
                          />
                          <FutureUseBadge pattern={detail.pattern} />
                          {detail.pattern.profile_state !== "current" ? (
                            <MatchingRuleBadge
                              state={detail.pattern.profile_state}
                            />
                          ) : null}
                          <Badge variant="outline">
                            {detail.pattern.environment}
                          </Badge>
                          <Badge variant="outline">
                            {detail.pattern.data_class === "operational"
                              ? "运营数据"
                              : "验证数据"}
                          </Badge>
                        </div>
                      </div>
                      {detail.pattern.memory_record ? (
                        <Button size="sm" asChild>
                          <Link
                            href={`/workspace/soc/memory/records/${encodeURIComponent(detail.pattern.memory_record.memory_id)}`}
                          >
                            <DatabaseIcon className="size-4" />
                            查看 / 修订经验
                            <ChevronRightIcon className="size-4" />
                          </Link>
                        </Button>
                      ) : detail.pattern.candidate ? (
                        <Button
                          variant={
                            ["pending_review", "confirmed_candidate"].includes(
                              detail.pattern.candidate.status,
                            )
                              ? "default"
                              : "secondary"
                          }
                          size="sm"
                          asChild
                        >
                          <Link
                            href={`/workspace/soc/review/memory-candidates/${detail.pattern.candidate.candidate_id}`}
                          >
                            <ShieldCheckIcon className="size-4" />
                            {["pending_review", "confirmed_candidate"].includes(
                              detail.pattern.candidate.status,
                            )
                              ? "审核并决定"
                              : "查看治理记录"}
                            <ChevronRightIcon className="size-4" />
                          </Link>
                        </Button>
                      ) : null}
                    </div>
                  </div>

                  {detail.pattern.attention_reasons.length > 0 ? (
                    <div className="border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm text-amber-900">
                      <div className="flex items-start gap-2">
                        <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
                        <div>
                          <div className="font-medium">需要治理关注</div>
                          <div className="mt-1 text-xs break-words">
                            {detail.pattern.attention_reasons
                              .map(
                                (reason) =>
                                  ATTENTION_REASON_LABELS[reason] ?? reason,
                              )
                              .join(" · ")}
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : null}

                  <dl className="grid border-b sm:grid-cols-2">
                    <div className="border-b px-5 py-3 sm:border-r">
                      <dt className="text-muted-foreground text-xs">
                        匹配规则版本
                      </dt>
                      <dd className="mt-1 text-sm">
                        {detail.pattern.profile_id} v
                        {detail.pattern.profile_version}
                      </dd>
                      {detail.pattern.profile_state === "legacy" ? (
                        <div className="text-muted-foreground mt-1 text-xs">
                          当前规则版本 v{detail.pattern.current_profile_version}
                          ，这条模式需要重新校验。
                        </div>
                      ) : detail.pattern.profile_state === "unregistered" ? (
                        <div className="mt-1 text-xs text-red-700">
                          当前系统无法识别该匹配规则，不能用于新告警。
                        </div>
                      ) : (
                        <div className="text-muted-foreground mt-1 text-xs">
                          用于从告警中提取稳定的同类特征；仅供版本审计。
                        </div>
                      )}
                    </div>
                    <div className="border-b px-5 py-3">
                      <dt className="text-muted-foreground text-xs">
                        样本积累
                      </dt>
                      <dd className="mt-1 text-sm tabular-nums">
                        {detail.pattern.support_count} 条告警 /{" "}
                        {detail.pattern.distinct_source_count} 个独立来源 /{" "}
                        {detail.pattern.aggregation_window_count} 个时间窗
                      </dd>
                    </div>
                    <div className="px-5 py-3 sm:border-r">
                      <dt className="text-muted-foreground text-xs">
                        候选生成记录
                      </dt>
                      <dd className="mt-1 text-sm tabular-nums">
                        生成时包含 {detail.pattern.candidate_snapshot_count}{" "}
                        条样本，后续新增 {detail.pattern.reinforcement_count} 条
                      </dd>
                    </div>
                    <div className="px-5 py-3">
                      <dt className="text-muted-foreground text-xs">
                        观察时间
                      </dt>
                      <dd className="mt-1 text-sm">
                        {formatTime(detail.pattern.first_observed_at)} →{" "}
                        {formatTime(detail.pattern.last_observed_at)}
                      </dd>
                    </div>
                  </dl>

                  {detail.suggested_successor_candidate_id &&
                  detail.pattern.candidate?.status !== "superseded" ? (
                    <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-3">
                      <div>
                        <div className="text-sm font-medium">
                          旧匹配规则候选处理
                        </div>
                        <div className="text-muted-foreground mt-1 text-xs">
                          同一来源告警已经使用当前匹配规则生成新候选{" "}
                          {detail.suggested_successor_candidate_id}
                        </div>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void handleSupersede()}
                        disabled={supersedeMutation.isPending}
                      >
                        <ArchiveIcon className="size-4" />
                        标记旧候选已替代
                      </Button>
                    </div>
                  ) : null}

                  <MemoryGovernanceStatus pattern={detail.pattern} />

                  <div>
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-3">
                      <h3 className="flex items-center gap-2 text-sm font-semibold">
                        <HistoryIcon className="size-4" />
                        来源告警
                      </h3>
                      <div className="flex items-center gap-2">
                        <span className="text-muted-foreground text-xs">
                          共 {detail.observation_total} 条
                        </span>
                        {!observationsVisible ? (
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              setObservationCursor({
                                lineageKey: selectedLineageKey,
                                offset: 0,
                                visible: true,
                              })
                            }
                          >
                            加载来源告警
                          </Button>
                        ) : null}
                      </div>
                    </div>
                    {observationsVisible ? (
                      <>
                        <div className="divide-y">
                          {detail.observations.map((observation) => (
                            <div
                              key={observation.observation_id}
                              className="grid grid-cols-[minmax(0,1fr)_8rem] gap-3 px-5 py-3 text-sm"
                            >
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="font-medium">
                                    Alert {observation.source.alert_id}
                                  </span>
                                  <Badge variant="outline">
                                    {observation.lesson?.verdict ??
                                      "unresolved"}
                                  </Badge>
                                </div>
                                <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                                  {observation.lesson?.summary ??
                                    observation.signature.label}
                                </p>
                              </div>
                              <div className="text-muted-foreground text-right text-xs">
                                <div>
                                  {formatTime(observation.source.observed_at)}
                                </div>
                                <div
                                  className="mt-1 font-mono"
                                  title={observation.source.run_id}
                                >
                                  {shortId(observation.source.run_id)}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                        {detail.observation_total > detail.observation_limit ? (
                          <div className="flex items-center justify-between border-t px-5 py-2">
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={
                                detailFetching ||
                                detail.observation_offset === 0
                              }
                              onClick={() =>
                                setObservationCursor({
                                  lineageKey: selectedLineageKey,
                                  offset: Math.max(
                                    0,
                                    detail.observation_offset -
                                      detail.observation_limit,
                                  ),
                                  visible: true,
                                })
                              }
                            >
                              <ChevronLeftIcon className="size-4" />
                              上一页
                            </Button>
                            <span className="text-muted-foreground text-xs tabular-nums">
                              {detail.observation_offset + 1}-
                              {Math.min(
                                detail.observation_offset +
                                  detail.observations.length,
                                detail.observation_total,
                              )}{" "}
                              / {detail.observation_total}
                            </span>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={
                                detailFetching ||
                                detail.observation_offset +
                                  detail.observation_limit >=
                                  detail.observation_total
                              }
                              onClick={() =>
                                setObservationCursor({
                                  lineageKey: selectedLineageKey,
                                  offset:
                                    detail.observation_offset +
                                    detail.observation_limit,
                                  visible: true,
                                })
                              }
                            >
                              下一页
                              <ChevronRightIcon className="size-4" />
                            </Button>
                          </div>
                        ) : null}
                      </>
                    ) : (
                      <div className="text-muted-foreground px-5 py-4 text-sm">
                        来源告警及其研判摘要按需加载，不影响当前经验状态查看。
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
