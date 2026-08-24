"use client";

import {
  ArchiveIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  DatabaseIcon,
  HistoryIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldCheckIcon,
  TriangleAlertIcon,
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
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useSocMemoryCenterOverview,
  useSocMemoryCenterPattern,
  useSupersedeSocMemoryCandidate,
} from "@/core/soc";
import type {
  SocMemoryCenterPatternSummary,
  SocMemoryPatternLifecycleState,
  SocMemoryProfileState,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;
const OBSERVATION_PAGE_SIZE = 20;

const PROFILE_LABELS: Record<SocMemoryProfileState, string> = {
  current: "当前 Profile",
  legacy: "旧 Profile",
  unregistered: "未注册 Profile",
};

const LIFECYCLE_LABELS: Record<SocMemoryPatternLifecycleState, string> = {
  collecting: "观察聚合中",
  candidate_pending: "候选待审",
  candidate_intermediate: "候选处理中",
  memory_inactive: "Memory 未启用",
  memory_active: "Memory 已启用",
  terminal_history: "历史终态",
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
}: {
  label: string;
  value: number;
  tone?: "neutral" | "attention" | "positive";
}) {
  return (
    <div
      className={cn(
        "min-w-0 border-r px-4 py-3 last:border-r-0",
        tone === "attention" &&
          value > 0 &&
          "bg-amber-50 text-amber-950 dark:bg-amber-950/30 dark:text-amber-100",
        tone === "positive" &&
          value > 0 &&
          "bg-emerald-50 text-emerald-950 dark:bg-emerald-950/30 dark:text-emerald-100",
      )}
    >
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function ProfileBadge({ state }: { state: SocMemoryProfileState }) {
  return (
    <Badge
      variant={state === "current" ? "secondary" : "outline"}
      className={cn(
        state === "legacy" && "border-amber-400 text-amber-700",
        state === "unregistered" && "border-red-400 text-red-700",
      )}
    >
      {PROFILE_LABELS[state]}
    </Badge>
  );
}

function LifecycleBadge({ state }: { state: SocMemoryPatternLifecycleState }) {
  return (
    <Badge
      variant={state === "memory_active" ? "default" : "outline"}
      className={cn(
        state === "candidate_pending" && "border-amber-400 text-amber-700",
      )}
    >
      {LIFECYCLE_LABELS[state]}
    </Badge>
  );
}

function patternTitle(pattern: SocMemoryCenterPatternSummary) {
  return (
    pattern.candidate?.summary ?? pattern.pattern_label ?? pattern.pattern_value
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
  }, [search, dataClass]);

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
            "Memory Center operator reconciled a same-alert candidate created by an older profile contract.",
        },
      });
      toast.success("旧 Profile 候选已标记为历史替代项");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Profile 对账失败");
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={BrainCircuitIcon}
        title="SOC Memory Center"
        description="管理重复模式、待审经验、确认 Memory 与 Profile 演进"
        actions={
          <>
            <Button size="sm" asChild>
              <Link href="/workspace/soc/review/memory-candidates">
                <ShieldCheckIcon className="size-4" />
                审核 Memory Candidate
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
              title="刷新 Memory Center"
              aria-label="刷新 Memory Center"
            >
              <RefreshCwIcon
                className={cn("size-4", isFetching && "animate-spin")}
              />
            </Button>
          </>
        }
      />

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[1600px] flex-col gap-4 p-4 md:p-6">
          <section
            className="grid overflow-hidden border sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-9"
            aria-label="Memory inventory metrics"
          >
            <Metric
              label="Pattern Lineages"
              value={overview?.metrics.pattern_count ?? 0}
            />
            <Metric
              label="24h Windows"
              value={overview?.metrics.aggregation_window_count ?? 0}
            />
            <Metric
              label="Observations"
              value={overview?.metrics.observation_count ?? 0}
            />
            <Metric
              label="待审候选"
              value={overview?.metrics.pending_candidate_count ?? 0}
              tone="attention"
            />
            <Metric
              label="已确认 Memory"
              value={overview?.metrics.confirmed_memory_count ?? 0}
              tone="positive"
            />
            <Metric
              label="检索已启用"
              value={overview?.metrics.retrieval_enabled_memory_count ?? 0}
            />
            <Metric
              label="已替代候选"
              value={overview?.metrics.superseded_candidate_count ?? 0}
            />
            <Metric
              label="旧 Profile"
              value={overview?.metrics.legacy_profile_pattern_count ?? 0}
            />
            <Metric
              label="未注册 Profile"
              value={overview?.metrics.unregistered_profile_pattern_count ?? 0}
            />
          </section>

          <section className="flex flex-wrap items-center gap-2 border px-3 py-3">
            <form
              className="flex min-w-0 flex-1 gap-2 sm:min-w-80"
              onSubmit={(event) => {
                event.preventDefault();
                setSearch(searchDraft.trim());
              }}
            >
              <Input
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                placeholder="搜索模式名称、Alert ID、lineage 或 Profile"
                aria-label="搜索 Memory patterns"
              />
              <Button type="submit" variant="outline" size="icon" title="搜索">
                <SearchIcon className="size-4" />
              </Button>
            </form>
            <Select
              value={dataClass}
              onValueChange={(value) =>
                setDataClass(value as "all" | "simulation" | "operational")
              }
            >
              <SelectTrigger className="w-40" aria-label="数据类型">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部数据</SelectItem>
                <SelectItem value="operational">Operational</SelectItem>
                <SelectItem value="simulation">Simulation</SelectItem>
              </SelectContent>
            </Select>
            {(overview?.terminal_history_count ?? 0) > 0 ? (
              <Button
                type="button"
                variant={showHistory ? "secondary" : "outline"}
                onClick={() => setShowHistory((value) => !value)}
              >
                <ArchiveIcon className="size-4" />
                {showHistory
                  ? "隐藏历史"
                  : `历史审计 (${overview?.terminal_history_count ?? 0})`}
              </Button>
            ) : null}
            <div className="text-muted-foreground ml-auto text-xs">
              {overview ? `${overview.total} patterns` : "-"}
            </div>
          </section>

          <section className="grid min-h-[38rem] border lg:grid-cols-[minmax(30rem,0.95fr)_minmax(28rem,1.05fr)]">
            <div className="min-w-0 border-b lg:border-r lg:border-b-0">
              <div className="bg-muted/30 grid grid-cols-[minmax(0,1fr)_7rem_7rem_6rem] border-b px-3 py-2 text-xs font-medium">
                <span>模式 / Pattern</span>
                <span>生命周期</span>
                <span>Profile</span>
                <span className="text-right">支持数</span>
              </div>
              {isLoading ? (
                <div className="text-muted-foreground flex h-48 items-center justify-center text-sm">
                  正在读取 Memory inventory...
                </div>
              ) : error ? (
                <div className="text-destructive flex h-48 items-center justify-center px-6 text-center text-sm">
                  {error instanceof Error
                    ? error.message
                    : "Memory Center 加载失败"}
                </div>
              ) : (overview?.items.length ?? 0) === 0 ? (
                <div className="text-muted-foreground flex h-48 items-center justify-center text-sm">
                  当前筛选下没有 Pattern lineage。
                </div>
              ) : (
                <div className="divide-y">
                  {overview?.items.map((pattern) => (
                    <Link
                      key={pattern.lineage_key}
                      href={`/workspace/soc/memory/patterns/${pattern.lineage_key}`}
                      className={cn(
                        "hover:bg-muted/40 grid min-h-20 grid-cols-[minmax(0,1fr)_7rem_7rem_6rem] items-center gap-2 px-3 py-3 text-sm",
                        selectedLineageKey === pattern.lineage_key &&
                          "bg-muted/60",
                      )}
                    >
                      <div className="min-w-0">
                        <div
                          className="truncate font-medium"
                          title={patternTitle(pattern)}
                        >
                          {patternTitle(pattern)}
                        </div>
                        <div className="text-muted-foreground mt-1 flex min-w-0 gap-2 text-xs">
                          <span>{pattern.pattern_dimension}</span>
                          <span
                            className="truncate font-mono"
                            title={pattern.lineage_key}
                          >
                            {shortId(pattern.lineage_key)}
                          </span>
                        </div>
                      </div>
                      <LifecycleBadge state={pattern.lifecycle_state} />
                      <ProfileBadge state={pattern.profile_state} />
                      <div className="text-right tabular-nums">
                        <div className="font-medium">
                          {pattern.support_count}
                        </div>
                        <div className="text-muted-foreground text-xs">
                          {pattern.aggregation_window_count} windows
                        </div>
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
                  {offset + 1}-
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
                  正在加载 Pattern 生命周期...
                </div>
              ) : detailError ? (
                <div className="text-destructive flex h-64 items-center justify-center px-6 text-center text-sm">
                  {detailError instanceof Error
                    ? detailError.message
                    : "Pattern 加载失败"}
                </div>
              ) : !detail ? (
                <div className="text-muted-foreground flex h-64 items-center justify-center text-sm">
                  选择一个 Pattern lineage 查看详情。
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
                          <ProfileBadge state={detail.pattern.profile_state} />
                          <Badge variant="outline">
                            {detail.pattern.environment}
                          </Badge>
                          <Badge variant="outline">
                            {detail.pattern.data_class}
                          </Badge>
                        </div>
                      </div>
                      {detail.pattern.candidate ? (
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
                            {detail.pattern.attention_reasons.join(" · ")}
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : null}

                  <dl className="grid border-b sm:grid-cols-2">
                    <div className="border-b px-5 py-3 sm:border-r">
                      <dt className="text-muted-foreground text-xs">
                        Profile Contract
                      </dt>
                      <dd className="mt-1 text-sm">
                        {detail.pattern.profile_id} v
                        {detail.pattern.profile_version}
                      </dd>
                      {detail.pattern.profile_state === "legacy" ? (
                        <div className="text-muted-foreground mt-1 text-xs">
                          当前版本 v{detail.pattern.current_profile_version}
                        </div>
                      ) : null}
                    </div>
                    <div className="border-b px-5 py-3">
                      <dt className="text-muted-foreground text-xs">
                        Pattern History
                      </dt>
                      <dd className="mt-1 text-sm tabular-nums">
                        {detail.pattern.support_count} observations /{" "}
                        {detail.pattern.distinct_source_count} distinct /{" "}
                        {detail.pattern.aggregation_window_count} windows
                      </dd>
                    </div>
                    <div className="px-5 py-3 sm:border-r">
                      <dt className="text-muted-foreground text-xs">
                        Candidate Snapshot
                      </dt>
                      <dd className="mt-1 text-sm tabular-nums">
                        {detail.pattern.candidate_snapshot_count} frozen +{" "}
                        {detail.pattern.reinforcement_count} reinforcement
                      </dd>
                    </div>
                    <div className="px-5 py-3">
                      <dt className="text-muted-foreground text-xs">
                        Observed
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
                          Profile 候选对账
                        </div>
                        <div className="text-muted-foreground mt-1 text-xs">
                          同一源告警已有当前 Profile 候选{" "}
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

                  <div className="border-b px-5 py-4">
                    <h3 className="flex items-center gap-2 text-sm font-semibold">
                      {detail.pattern.memory_record?.retrieval_enabled ? (
                        <CheckCircle2Icon className="size-4" />
                      ) : (
                        <DatabaseIcon className="size-4" />
                      )}
                      Memory 状态
                    </h3>
                    <p className="text-muted-foreground mt-2 text-sm">
                      {detail.pattern.memory_record
                        ? `${detail.pattern.memory_record.memory_id} · ${detail.pattern.memory_record.retrieval_enabled ? "已进入检索" : "已确认，尚未启用检索"}`
                        : detail.pattern.candidate
                          ? `${detail.pattern.candidate.candidate_id} · ${detail.pattern.candidate.status}`
                          : "尚未达到候选生成与专家审核阶段。"}
                    </p>
                  </div>

                  <div>
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-3">
                      <h3 className="flex items-center gap-2 text-sm font-semibold">
                        <HistoryIcon className="size-4" />
                        Source Observations
                      </h3>
                      <div className="flex items-center gap-2">
                        <span className="text-muted-foreground text-xs">
                          {detail.observation_total} total
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
                            加载来源观察
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
                        来源告警及其研判摘要按需加载，不影响 Pattern 与 Memory
                        治理信息查看。
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
