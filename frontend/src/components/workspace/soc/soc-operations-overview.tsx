"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  CheckCircle2Icon,
  Clock3Icon,
  DatabaseIcon,
  GaugeIcon,
  RefreshCwIcon,
  RadioTowerIcon,
  ShieldCheckIcon,
  WrenchIcon,
} from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSocOperationsSnapshot } from "@/core/soc";
import type {
  SocOperationsAvailability,
  SocOperationsSnapshot,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const AVAILABILITY_LABELS: Record<SocOperationsAvailability, string> = {
  available: "Available / 可用",
  unavailable: "Unavailable / 不可用",
  not_configured: "Not configured / 未配置",
  not_measured: "Not measured / 未测量",
};

const AVAILABILITY_CLASSES: Record<SocOperationsAvailability, string> = {
  available: "border-emerald-300 bg-emerald-50 text-emerald-800",
  unavailable: "border-red-300 bg-red-50 text-red-800",
  not_configured: "border-zinc-300 bg-zinc-50 text-zinc-700",
  not_measured: "border-amber-300 bg-amber-50 text-amber-800",
};

function AvailabilityBadge({
  availability,
}: {
  availability: SocOperationsAvailability;
}) {
  return (
    <Badge
      variant="outline"
      className={cn("font-normal", AVAILABILITY_CLASSES[availability])}
    >
      {AVAILABILITY_LABELS[availability]}
    </Badge>
  );
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function yesNo(value: boolean) {
  return value ? "Yes / 是" : "No / 否";
}

function MetricCell({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: number | string;
  emphasis?: "critical" | "warning";
}) {
  return (
    <div className="min-w-0 border-r border-b px-4 py-3 last:border-r-0 md:px-5">
      <div className="text-muted-foreground text-xs leading-5">{label}</div>
      <div
        className={cn(
          "mt-1 text-xl font-semibold tabular-nums",
          emphasis === "critical" && "text-red-700",
          emphasis === "warning" && "text-amber-700",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid min-h-10 grid-cols-[minmax(7.5rem,11rem)_minmax(0,1fr)] items-center gap-4 border-b py-2 text-sm last:border-b-0">
      <div className="text-muted-foreground">{label}</div>
      <div className="min-w-0 break-words">{children}</div>
    </div>
  );
}

function DataNature({ snapshot }: { snapshot: SocOperationsSnapshot }) {
  const backend = snapshot.persisted.backend?.toLowerCase() ?? null;
  const persistenceNature =
    backend === "sqlite"
      ? "SQLite local/test evidence / SQLite 本地或仿真证据"
      : backend
        ? `${snapshot.persisted.backend} persistence / 持久化数据（环境级别未声明）`
        : "Persistence environment not declared / 未声明持久化环境";

  return (
    <section
      data-testid="operations-data-nature"
      className="bg-muted/35 border-b px-5 py-4 md:px-7"
      aria-labelledby="snapshot-boundary-heading"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-4xl">
          <div className="flex items-center gap-2">
            <GaugeIcon className="size-4" />
            <h2 id="snapshot-boundary-heading" className="text-sm font-medium">
              Passive snapshot / 被动只读快照
            </h2>
          </div>
          <p className="text-muted-foreground mt-1 text-sm leading-6">
            {persistenceNature}。页面只展示后端契约返回的观察值，不主动探测
            Kafka，
            不在浏览器计算总体健康度，也不把本地或仿真数据解释为生产证据。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{snapshot.schema_version}</Badge>
          <Badge variant="outline">
            Overall health / 总体健康：not provided
          </Badge>
        </div>
      </div>
    </section>
  );
}

function LoadingState() {
  return (
    <div className="space-y-6 p-6" aria-label="正在加载 SOC 运营快照">
      <Skeleton className="h-20 w-full rounded-md" />
      <div className="grid grid-cols-2 gap-px border md:grid-cols-4 xl:grid-cols-7">
        {Array.from({ length: 7 }, (_, index) => (
          <Skeleton key={index} className="h-24 rounded-none" />
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Skeleton className="h-72 w-full rounded-md" />
        <Skeleton className="h-72 w-full rounded-md" />
      </div>
    </div>
  );
}

export function SocOperationsOverview() {
  const query = useSocOperationsSnapshot();
  const snapshot = query.snapshot;
  const metrics = snapshot?.persisted.metrics ?? null;
  const runStatuses = Object.entries(
    metrics?.analysis_run_status_counts ?? {},
  ).sort(([left], [right]) => left.localeCompare(right));

  return (
    <div className="flex size-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4 md:px-7">
        <div className="flex items-center gap-3">
          <ActivityIcon className="size-5" />
          <div>
            <h1 className="text-xl font-semibold">SOC 运营观察</h1>
            <p className="text-muted-foreground mt-0.5 text-sm">
              Operations overview
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link href="/workspace/soc/review">
              <ShieldCheckIcon className="size-4" />
              告警复核
            </Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link href="/workspace/soc/normalization">
              <WrenchIcon className="size-4" />
              归一化
            </Link>
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
            aria-label="刷新运营快照"
            title="刷新运营快照"
          >
            <RefreshCwIcon
              className={cn("size-4", query.isFetching && "animate-spin")}
            />
          </Button>
        </div>
      </header>

      <main
        data-testid="soc-operations-scroll"
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {query.isLoading && !snapshot ? <LoadingState /> : null}

        {query.error && !snapshot ? (
          <section className="border-b border-red-200 bg-red-50 px-5 py-5 text-red-800 md:px-7">
            <div className="flex items-start gap-3">
              <AlertTriangleIcon className="mt-0.5 size-5 shrink-0" />
              <div>
                <h2 className="font-medium">运营快照加载失败</h2>
                <p className="mt-1 text-sm">
                  {query.error instanceof Error
                    ? query.error.message
                    : "无法读取后端运营快照。"}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => void query.refetch()}
                >
                  <RefreshCwIcon className="size-4" />
                  重试
                </Button>
              </div>
            </div>
          </section>
        ) : null}

        {snapshot ? (
          <>
            <DataNature snapshot={snapshot} />

            {query.error ? (
              <div className="border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm text-amber-900 md:px-7">
                自动刷新失败，当前继续展示最近一次成功快照。
              </div>
            ) : null}

            <section aria-labelledby="workload-heading">
              <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-4 md:px-7">
                <div>
                  <h2 id="workload-heading" className="font-medium">
                    Workload snapshot / 业务负载快照
                  </h2>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {metrics?.measurement_scope === "lifetime"
                      ? "Lifetime exact aggregates / 全生命周期精确聚合"
                      : "Persisted metrics unavailable / 持久化指标不可用"}
                  </p>
                </div>
                <AvailabilityBadge
                  availability={snapshot.persisted.availability}
                />
              </div>

              {metrics ? (
                <div className="grid grid-cols-2 border-y md:grid-cols-4 xl:grid-cols-7">
                  <MetricCell
                    label="Analysis runs / 分析运行"
                    value={metrics.analysis_run_count}
                  />
                  <MetricCell
                    label="Open review / 待复核"
                    value={metrics.open_review_count}
                    emphasis={
                      metrics.open_review_count > 0 ? "warning" : undefined
                    }
                  />
                  <MetricCell
                    label="Pending approval / 待审批"
                    value={metrics.pending_approval_request_count}
                    emphasis={
                      metrics.pending_approval_request_count > 0
                        ? "warning"
                        : undefined
                    }
                  />
                  <MetricCell
                    label="Normalization / 归一化问题"
                    value={metrics.open_normalization_issue_count}
                  />
                  <MetricCell
                    label="Critical mapping / 严重映射问题"
                    value={metrics.critical_open_normalization_issue_count}
                    emphasis={
                      metrics.critical_open_normalization_issue_count > 0
                        ? "critical"
                        : undefined
                    }
                  />
                  <MetricCell
                    label="Memory review / 待审记忆"
                    value={metrics.pending_memory_candidate_count}
                  />
                  <MetricCell
                    label="Active baselines / 有效基线"
                    value={metrics.active_normalization_baseline_count}
                  />
                </div>
              ) : (
                <div className="border-y px-5 py-5 text-sm md:px-7">
                  <div className="flex items-center gap-2 font-medium">
                    <AlertTriangleIcon className="size-4 text-amber-700" />
                    持久化聚合不可用
                  </div>
                  <p className="text-muted-foreground mt-1">
                    {snapshot.persisted.error_code ??
                      "The backend did not provide persisted metrics."}
                  </p>
                </div>
              )}
            </section>

            <section className="grid border-b lg:grid-cols-2">
              <div className="border-b px-5 py-5 md:px-7 lg:border-r lg:border-b-0">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <DatabaseIcon className="size-4" />
                    <h2 className="font-medium">Persistence / 持久化</h2>
                  </div>
                  <AvailabilityBadge
                    availability={snapshot.persisted.availability}
                  />
                </div>
                <div className="mt-4 border-y">
                  <DetailRow label="Backend / 后端">
                    <span className="font-mono text-xs">
                      {snapshot.persisted.backend ?? "-"}
                    </span>
                  </DetailRow>
                  <DetailRow label="Generated / 生成时间">
                    {formatDateTime(snapshot.generated_at)}
                  </DetailRow>
                  <DetailRow label="Latest start / 最近开始">
                    {formatDateTime(metrics?.latest_analysis_started_at)}
                  </DetailRow>
                  <DetailRow label="Latest completion / 最近完成">
                    {formatDateTime(metrics?.latest_analysis_completed_at)}
                  </DetailRow>
                  <DetailRow label="Oldest review / 最早待复核">
                    {formatDateTime(metrics?.oldest_open_review_created_at)}
                  </DetailRow>
                </div>
                <div className="mt-4">
                  <div className="text-muted-foreground text-xs">
                    Run status counts / 运行状态计数
                  </div>
                  {runStatuses.length ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {runStatuses.map(([status, count]) => (
                        <Badge key={status} variant="secondary">
                          {status}: {count}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <div className="text-muted-foreground mt-2 text-sm">-</div>
                  )}
                </div>
              </div>

              <div className="px-5 py-5 md:px-7">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <RadioTowerIcon className="size-4" />
                    <h2 className="font-medium">
                      Kafka ingestion / Kafka 接入
                    </h2>
                  </div>
                  <AvailabilityBadge
                    availability={snapshot.kafka.availability}
                  />
                </div>
                <div className="mt-4 border-y">
                  <DetailRow label="Enabled / 已启用">
                    {yesNo(snapshot.kafka.enabled)}
                  </DetailRow>
                  <DetailRow label="Settings / 配置有效">
                    {yesNo(snapshot.kafka.settings_valid)}
                  </DetailRow>
                  <DetailRow label="Broker check / 连接探测">
                    {snapshot.kafka.checked
                      ? `Checked / 已探测；reachable=${String(snapshot.kafka.reachable)}`
                      : "Passive API does not probe / 被动 API 未探测"}
                  </DetailRow>
                  <DetailRow label="Bootstrap / 引导节点">
                    {snapshot.kafka.bootstrap_server_count}
                  </DetailRow>
                  <DetailRow label="Topics / 主题">
                    Alert {snapshot.kafka.alert_topic_count} · Approval{" "}
                    {snapshot.kafka.approval_request_topic_count}
                  </DetailRow>
                  <DetailRow label="DLQ / 死信队列">
                    {yesNo(snapshot.kafka.dead_letter_configured)}
                  </DetailRow>
                  <DetailRow label="Consumer lag / 消费积压">
                    <AvailabilityBadge
                      availability={snapshot.kafka.consumer_lag_availability}
                    />
                  </DetailRow>
                </div>
                {snapshot.kafka.error_code ? (
                  <p className="mt-3 font-mono text-xs text-amber-800">
                    {snapshot.kafka.error_code}
                  </p>
                ) : null}
              </div>
            </section>

            <section
              className="px-5 py-5 md:px-7"
              aria-labelledby="gaps-heading"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Clock3Icon className="size-4" />
                  <div>
                    <h2 id="gaps-heading" className="font-medium">
                      Measurement gaps / 尚未采集
                    </h2>
                    <p className="text-muted-foreground mt-1 text-xs">
                      缺少测量不等于健康，也不等于故障。
                    </p>
                  </div>
                </div>
                <Badge variant="outline">
                  Production SLO evidence:{" "}
                  {snapshot.production_slo_evidence_available
                    ? "available"
                    : "not available"}
                </Badge>
              </div>
              <div className="mt-4 divide-y border-y">
                {snapshot.measurement_gaps.map((gap) => (
                  <div
                    key={gap.metric}
                    className="grid gap-3 py-3 text-sm md:grid-cols-[15rem_9rem_minmax(0,1fr)] md:items-center"
                  >
                    <span className="font-mono text-xs">{gap.metric}</span>
                    <AvailabilityBadge availability={gap.availability} />
                    <span className="text-muted-foreground">{gap.reason}</span>
                  </div>
                ))}
              </div>
              <div className="text-muted-foreground mt-4 flex items-start gap-2 text-xs leading-5">
                <CheckCircle2Icon className="mt-0.5 size-3.5 shrink-0" />
                Snapshot refreshes every 30 seconds. Counts remain server-owned;
                the browser performs no aggregation or operational decision.
              </div>
            </section>
          </>
        ) : null}
      </main>
    </div>
  );
}
