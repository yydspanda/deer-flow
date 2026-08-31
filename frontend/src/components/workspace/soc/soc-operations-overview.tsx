"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BookOpenCheckIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  Clock3Icon,
  CpuIcon,
  DatabaseIcon,
  GaugeIcon,
  GitBranchIcon,
  RefreshCwIcon,
  RadioTowerIcon,
  ShieldCheckIcon,
  TargetIcon,
} from "lucide-react";
import { Fragment, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useSocEffectivenessSnapshot,
  useSocOperationsSnapshot,
  useSocRuleEffectivenessDetail,
} from "@/core/soc";
import type {
  SocEffectivenessSnapshot,
  SocMemoryEffectiveness,
  SocOperationsAvailability,
  SocOperationsSnapshot,
  SocRateMetric,
  SocRuleEffectivenessDetail,
  SocRuleRecommendationPriority,
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

function formatRate(metric: SocRateMetric) {
  return metric.value === null || metric.value === undefined
    ? "待标注"
    : new Intl.NumberFormat("zh-CN", {
        style: "percent",
        maximumFractionDigits: 1,
      }).format(metric.value);
}

function formatOptionalRate(value?: number | null) {
  return value === null || value === undefined
    ? "-"
    : new Intl.NumberFormat("zh-CN", {
        style: "percent",
        maximumFractionDigits: 1,
      }).format(value);
}

function compactNumber(value: number) {
  return new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function RateMetricStat({
  label,
  metric,
  direction = "neutral",
  primary = false,
}: {
  label: string;
  metric: SocRateMetric;
  direction?: "higher" | "lower" | "neutral";
  primary?: boolean;
}) {
  const measured = metric.value !== null && metric.value !== undefined;
  return (
    <div className={cn("min-w-0", !primary && "border-t pt-3")}>
      <div className="text-muted-foreground text-xs leading-5">{label}</div>
      <div
        className={cn(
          "mt-1 font-semibold tabular-nums",
          primary ? "text-3xl" : "text-lg",
          !measured && "text-muted-foreground text-lg",
          measured && direction === "higher" && "text-emerald-700",
          measured &&
            direction === "lower" &&
            metric.value! > 0 &&
            "text-amber-700",
        )}
      >
        {measured ? formatRate(metric) : "--"}
      </div>
      {measured ? (
        <div className="text-muted-foreground mt-1 text-[11px] tabular-nums">
          {metric.numerator} / {metric.denominator}
        </div>
      ) : (
        <div className="mt-1 h-4" aria-hidden="true" />
      )}
    </div>
  );
}

function EffectivenessMetricGroup({
  icon,
  title,
  question,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  question: string;
  children: React.ReactNode;
}) {
  return (
    <article
      data-testid="effectiveness-metric-group"
      className="grid min-h-64 min-w-0 grid-rows-[auto_1fr] border-r border-b px-5 py-5 last:border-r-0"
    >
      <div>
        <div className="flex items-center gap-2">
          {icon}
          <h3 className="text-sm font-semibold">{title}</h3>
        </div>
        <p className="text-muted-foreground mt-1 min-h-10 text-xs leading-5">
          {question}
        </p>
      </div>
      <div className="mt-4 grid content-between gap-3">{children}</div>
    </article>
  );
}

const RECOMMENDATION_CLASSES: Record<SocRuleRecommendationPriority, string> = {
  high: "border-red-300 bg-red-50 text-red-800",
  medium: "border-amber-300 bg-amber-50 text-amber-800",
  low: "border-sky-300 bg-sky-50 text-sky-800",
  info: "border-zinc-300 bg-zinc-50 text-zinc-700",
};

const VERDICT_LABELS: Record<string, string> = {
  false_positive: "误报",
  true_positive: "真实风险",
  suspicious: "可疑",
  unknown: "待确认",
};

function formatVerdictCounts(verdictCounts: Record<string, number>) {
  const values = Object.entries(verdictCounts)
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1])
    .map(
      ([verdict, count]) => `${VERDICT_LABELS[verdict] ?? verdict} ${count}`,
    );
  return values.length > 0 ? values.join(" · ") : "暂无结论";
}

function memoryStageLabel({
  retrieval_enabled: retrievalEnabled,
  memory_id: memoryId,
  candidate_id: candidateId,
}: {
  retrieval_enabled: boolean;
  memory_id?: string | null;
  candidate_id?: string | null;
}) {
  if (retrievalEnabled) return "已启用，可精确复用";
  if (memoryId) return "经验已确认，暂停复用";
  if (candidateId) return "经验待审核";
  return "正在积累同类样本";
}

function MemoryEffectRow({ memory }: { memory: SocMemoryEffectiveness }) {
  return (
    <tr className="align-top">
      <td className="max-w-80 px-3 py-3">
        <div className="font-medium break-words">
          {memory.summary ?? memory.memory_id}
        </div>
        <div className="text-muted-foreground mt-1 font-mono">
          {memory.memory_id} · v{memory.memory_version}
        </div>
        <Badge
          variant="outline"
          className={cn(
            "mt-2",
            memory.retrieval_enabled
              ? "border-emerald-300 bg-emerald-50 text-emerald-800"
              : "border-zinc-300 bg-zinc-50 text-zinc-700",
          )}
        >
          {memory.retrieval_enabled ? "可精确复用" : "未启用精确复用"}
        </Badge>
      </td>
      <td className="px-3 py-3 tabular-nums">
        直接复用 {memory.directive_count}
        <div className="text-muted-foreground mt-1">
          仅供参考 {memory.context_only_count}
        </div>
      </td>
      <td className="px-3 py-3 tabular-nums">
        {formatRate(memory.final_outcome_coverage)}
        <div className="text-muted-foreground mt-1">
          {memory.high_trust_feedback_count}/{memory.use_alert_count}{" "}
          条有最终反馈
        </div>
      </td>
      <td className="px-3 py-3 tabular-nums">
        {formatRate(memory.directive_accuracy)}
        <div className="text-muted-foreground mt-1">
          只统计直接复用且已有最终反馈的告警
        </div>
      </td>
      <td className="px-3 py-3 tabular-nums">
        帮助纠正 {memory.helpful_correction_count}
        <div
          className={cn(
            "mt-1",
            memory.harmful_override_count > 0
              ? "text-red-700"
              : "text-muted-foreground",
          )}
        >
          错误覆盖 {memory.harmful_override_count}
        </div>
        <div
          className={cn(
            "mt-1",
            memory.contradiction_count > 0
              ? "text-amber-700"
              : "text-muted-foreground",
          )}
        >
          运营结论相反 {memory.contradiction_count}
        </div>
        {memory.wrong_auto_ignore_count > 0 ? (
          <div className="mt-1 font-medium text-red-700">
            错误自动忽略 {memory.wrong_auto_ignore_count}
          </div>
        ) : null}
      </td>
      <td className="max-w-64 px-3 py-3">
        <div className="break-words">
          来源：{memory.source_rule_codes.join("、") || "未记录"}
        </div>
        <div className="text-muted-foreground mt-1 break-words">
          实际用于：{memory.actual_rule_codes.join("、") || "暂无"}
        </div>
      </td>
    </tr>
  );
}

function RuleEffectivenessDrilldown({
  detail,
  isLoading,
  error,
}: {
  detail: SocRuleEffectivenessDetail | null;
  isLoading: boolean;
  error: unknown;
}) {
  if (isLoading && !detail) {
    return <Skeleton className="h-40 w-full rounded-none" />;
  }
  if (error && !detail) {
    return (
      <div className="border-l-2 border-red-500 bg-red-50 px-4 py-3 text-sm text-red-800">
        无法读取该规则的同类行为与 Memory 效果：
        {error instanceof Error ? error.message : "后端读模型不可用"}
      </div>
    );
  }
  if (!detail) return null;

  return (
    <div className="space-y-5 py-2">
      <div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <GitBranchIcon className="size-4" />
            <h4 className="font-semibold">同一 Rule Code 下的同类行为</h4>
          </div>
          <span className="text-muted-foreground">
            {detail.behavior_groups.length} 组行为 · 点击规则可收起
          </span>
        </div>
        <div className="bg-background mt-3 overflow-auto border">
          <table className="w-full min-w-[68rem] text-left text-xs">
            <thead className="bg-muted/70 border-b">
              <tr>
                <th className="px-3 py-2 font-medium">同类行为</th>
                <th className="px-3 py-2 font-medium">告警样本</th>
                <th className="px-3 py-2 font-medium">运营结论</th>
                <th className="px-3 py-2 font-medium">经验沉淀与使用</th>
                <th className="px-3 py-2 font-medium">最近出现</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {detail.behavior_groups.map((behavior) => (
                <tr key={behavior.lineage_key} className="align-top">
                  <td className="max-w-96 px-3 py-3 font-medium break-words">
                    {behavior.behavior_label}
                  </td>
                  <td className="px-3 py-3 tabular-nums">
                    {behavior.distinct_alert_count} 条告警
                    <div className="text-muted-foreground mt-1">
                      {behavior.window_count} 个时间窗口
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    {formatVerdictCounts(behavior.verdict_counts)}
                  </td>
                  <td className="px-3 py-3">
                    <Badge
                      variant="outline"
                      className={cn(
                        behavior.retrieval_enabled
                          ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                          : behavior.candidate_id
                            ? "border-amber-300 bg-amber-50 text-amber-800"
                            : "border-zinc-300 bg-zinc-50 text-zinc-700",
                      )}
                    >
                      {memoryStageLabel(behavior)}
                    </Badge>
                    {behavior.memory_id ? (
                      <div className="text-muted-foreground mt-1 font-mono">
                        {behavior.memory_id} · v{behavior.memory_version}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-3 tabular-nums">
                    {formatDateTime(behavior.last_observed_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {detail.behavior_groups.length === 0 ? (
            <div className="text-muted-foreground px-4 py-6 text-center text-sm">
              当前窗口尚未形成可比较的同类行为样本。
            </div>
          ) : null}
        </div>
      </div>

      <div>
        <div className="flex items-center gap-2">
          <BookOpenCheckIcon className="size-4" />
          <h4 className="font-semibold">Memory 实际效果</h4>
        </div>
        <p className="text-muted-foreground mt-1 text-xs leading-5">
          “直接复用结论”可以归因；“仅供研判参考”只说明模型看过，不能据此宣称准确率提升。
        </p>
        <div className="bg-background mt-3 overflow-auto border">
          <table className="w-full min-w-[76rem] text-left text-xs">
            <thead className="bg-muted/70 border-b">
              <tr>
                <th className="px-3 py-2 font-medium">Memory</th>
                <th className="px-3 py-2 font-medium">使用方式</th>
                <th className="px-3 py-2 font-medium">最终反馈覆盖</th>
                <th className="px-3 py-2 font-medium">直接复用正确率</th>
                <th className="px-3 py-2 font-medium">帮助与风险</th>
                <th className="px-3 py-2 font-medium">Rule Code 覆盖</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {detail.memories.map((memory) => (
                <MemoryEffectRow
                  key={`${memory.memory_id}:${memory.memory_version}`}
                  memory={memory}
                />
              ))}
            </tbody>
          </table>
          {detail.memories.length === 0 ? (
            <div className="text-muted-foreground px-4 py-6 text-center text-sm">
              该规则当前没有被实际使用过的 Memory。
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function EffectivenessSection({
  snapshot,
  isLoading,
  error,
  windowDays,
  onWindowChange,
}: {
  snapshot: SocEffectivenessSnapshot | null;
  isLoading: boolean;
  error: unknown;
  windowDays: number;
  onWindowChange: (days: number) => void;
}) {
  const summary = snapshot?.summary ?? null;
  const coverage = snapshot?.coverage ?? null;
  const compute = snapshot?.compute ?? null;
  const [expandedRuleKey, setExpandedRuleKey] = useState<string | null>(null);
  const ruleDetailQuery = useSocRuleEffectivenessDetail(
    expandedRuleKey,
    windowDays,
  );

  return (
    <section className="border-b" aria-labelledby="effectiveness-heading">
      <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-5 md:px-7">
        <div className="max-w-4xl">
          <div className="flex items-center gap-2">
            <TargetIcon className="size-4" />
            <h2 id="effectiveness-heading" className="font-medium">
              Effectiveness / 研判效能
            </h2>
          </div>
          <p className="text-muted-foreground mt-1 text-sm leading-6">
            以运营最终处置结果评估研判质量，以实际执行记录衡量自动化安全与减负效果。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <ToggleGroup
            type="single"
            variant="outline"
            value={String(windowDays)}
            onValueChange={(value) => {
              if (value) onWindowChange(Number(value));
            }}
            aria-label="选择效能统计窗口"
          >
            <ToggleGroupItem value="7">7 天</ToggleGroupItem>
            <ToggleGroupItem value="30">30 天</ToggleGroupItem>
            <ToggleGroupItem value="90">90 天</ToggleGroupItem>
          </ToggleGroup>
          {snapshot ? (
            <AvailabilityBadge availability={snapshot.availability} />
          ) : null}
        </div>
      </div>

      {isLoading && !snapshot ? (
        <div className="grid grid-cols-1 border-t sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} className="h-64 rounded-none border-r" />
          ))}
        </div>
      ) : null}

      {error && !snapshot ? (
        <div className="border-t border-red-200 bg-red-50 px-5 py-4 text-sm text-red-800 md:px-7">
          无法读取效能数据：
          {error instanceof Error ? error.message : "后端读模型不可用"}
        </div>
      ) : null}

      {snapshot && (!summary || !coverage || !compute) ? (
        <div className="border-t bg-amber-50 px-5 py-4 text-sm text-amber-900 md:px-7">
          当前数据库未提供可聚合的效能数据。
          {snapshot.error_code ? ` ${snapshot.error_code}` : ""}
        </div>
      ) : null}

      {snapshot && summary && coverage && compute ? (
        <>
          <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 border-t border-b bg-zinc-50 px-5 py-3 text-sm md:px-7 dark:bg-zinc-950/30">
            <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
              <span>
                已处理告警{" "}
                <strong className="text-lg tabular-nums">
                  {coverage.completed_alert_count}
                </strong>
              </span>
              <span className="text-muted-foreground text-xs">
                去除 {coverage.superseded_run_count} 次重跑
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4">
            <EffectivenessMetricGroup
              icon={<ShieldCheckIcon className="size-4 text-emerald-700" />}
              title="研判质量"
              question="有最终结果的样本里，系统判断是否正确？"
            >
              <RateMetricStat
                label="研判准确率 / Accuracy"
                metric={summary.triage_accuracy}
                direction="higher"
                primary
              />
              <RateMetricStat
                label="技术漏报率 / Miss rate"
                metric={summary.detection_miss_rate}
                direction="lower"
              />
            </EffectivenessMetricGroup>

            <EffectivenessMetricGroup
              icon={<AlertTriangleIcon className="size-4 text-red-700" />}
              title="自动化安全"
              question="实际自动忽略是否放过了真实攻击？"
            >
              <div className="min-w-0">
                <div className="text-muted-foreground text-xs leading-5">
                  错误自动忽略
                </div>
                <div
                  className={cn(
                    "mt-1 text-3xl font-semibold tabular-nums",
                    summary.operational_miss_rate.value === null ||
                      summary.operational_miss_rate.value === undefined
                      ? "text-muted-foreground text-lg"
                      : summary.operational_miss_rate.numerator > 0
                        ? "text-red-700"
                        : "text-emerald-700",
                  )}
                >
                  {summary.operational_miss_rate.value === null ||
                  summary.operational_miss_rate.value === undefined
                    ? "--"
                    : `${summary.operational_miss_rate.numerator} 条`}
                </div>
                {summary.operational_miss_rate.value !== null &&
                summary.operational_miss_rate.value !== undefined ? (
                  <div className="text-muted-foreground mt-1 text-[11px] tabular-nums">
                    攻击误忽略率 {formatRate(summary.operational_miss_rate)} ·{" "}
                    {summary.operational_miss_rate.numerator} /{" "}
                    {summary.operational_miss_rate.denominator}
                  </div>
                ) : (
                  <div className="mt-1 h-4" aria-hidden="true" />
                )}
              </div>
              <RateMetricStat
                label="自动忽略错误率 / Wrong ignore"
                metric={summary.wrong_auto_ignore_rate}
                direction="lower"
              />
            </EffectivenessMetricGroup>

            <EffectivenessMetricGroup
              icon={<GitBranchIcon className="size-4 text-sky-700" />}
              title="转交质量"
              question="转交是否集中在真实风险，并覆盖应转交的攻击？"
            >
              <RateMetricStat
                label="转交精确率 / Precision"
                metric={summary.transfer_precision}
                direction="higher"
                primary
              />
              <RateMetricStat
                label="攻击转交召回 / Recall"
                metric={summary.attack_transfer_recall}
                direction="higher"
              />
            </EffectivenessMetricGroup>

            <EffectivenessMetricGroup
              icon={<ActivityIcon className="size-4 text-violet-700" />}
              title="减负效果"
              question="系统实际自动处理多少，运营仍需触达多少？"
            >
              <RateMetricStat
                label="自动忽略率 / Automation"
                metric={summary.auto_ignore_rate}
                primary
              />
              <RateMetricStat
                label="人工触达率 / Human touch"
                metric={summary.human_touch_rate}
                direction="lower"
              />
            </EffectivenessMetricGroup>
          </div>

          <div className="grid border-t lg:grid-cols-[minmax(0,0.85fr)_minmax(0,2.15fr)]">
            <div className="border-b px-5 py-5 md:px-7 lg:border-r lg:border-b-0">
              <div className="flex items-center gap-2">
                <CpuIcon className="size-4" />
                <h3 className="text-sm font-semibold">
                  Compute efficiency / 算力利用
                </h3>
              </div>
              <dl className="mt-4 grid grid-cols-2 border-y text-sm">
                <div className="border-r border-b px-3 py-3">
                  <dt className="text-muted-foreground text-xs">模型调用</dt>
                  <dd className="mt-1 text-lg font-semibold tabular-nums">
                    {compute.provider_call_count}
                  </dd>
                </div>
                <div className="border-b px-3 py-3">
                  <dt className="text-muted-foreground text-xs">
                    Total tokens
                  </dt>
                  <dd className="mt-1 text-lg font-semibold tabular-nums">
                    {compactNumber(compute.total_tokens)}
                  </dd>
                </div>
                <div className="border-r px-3 py-3">
                  <dt className="text-muted-foreground text-xs">
                    平均 Run 耗时
                  </dt>
                  <dd className="mt-1 font-medium tabular-nums">
                    {compute.average_total_duration_ms === null ||
                    compute.average_total_duration_ms === undefined
                      ? "-"
                      : `${(compute.average_total_duration_ms / 1000).toFixed(1)} s`}
                  </dd>
                </div>
                <div className="px-3 py-3">
                  <dt className="text-muted-foreground text-xs">Token 覆盖</dt>
                  <dd className="mt-1 font-medium">
                    {formatRate(compute.token_measurement_coverage)}
                  </dd>
                </div>
              </dl>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <Badge variant="outline">
                  Repair {formatRate(compute.repair_rate)}
                </Badge>
                <Badge variant="outline">
                  Fallback {formatRate(compute.fallback_rate)}
                </Badge>
                <Badge variant="outline">
                  Degraded {formatRate(compute.degraded_rate)}
                </Badge>
              </div>
            </div>

            <div className="min-w-0 px-5 py-5 md:px-7">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <GitBranchIcon className="size-4" />
                  <div>
                    <h3 className="text-sm font-semibold">
                      Detection effectiveness / 检测规则效能
                    </h3>
                    <p className="text-muted-foreground mt-1 text-xs">
                      平安按 Rule Code
                      汇总；每条规则可下钻到多组同类行为和各自独立的 Memory。
                    </p>
                  </div>
                </div>
                <Badge
                  variant="outline"
                  title={snapshot.recommendation_policy_version}
                >
                  建议口径 v1
                </Badge>
              </div>
              <div className="mt-4 max-h-[34rem] overflow-auto border">
                <table className="w-full min-w-[80rem] text-left text-xs">
                  <thead className="bg-muted/70 sticky top-0 z-10 border-b">
                    <tr>
                      <th className="px-3 py-2 font-medium">
                        规则 / Rule Code
                      </th>
                      <th className="px-3 py-2 font-medium">量级</th>
                      <th className="px-3 py-2 font-medium">已有结果</th>
                      <th className="px-3 py-2 font-medium">有效检出</th>
                      <th className="px-3 py-2 font-medium">规则误报</th>
                      <th className="px-3 py-2 font-medium">AI 研判</th>
                      <th className="px-3 py-2 font-medium">自动忽略</th>
                      <th className="px-3 py-2 font-medium">Tokens</th>
                      <th className="px-3 py-2 font-medium">Memory</th>
                      <th className="px-3 py-2 font-medium">建议</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {snapshot.rules.slice(0, 100).map((rule) => (
                      <Fragment key={rule.group_key}>
                        <tr className="align-top">
                          <td className="max-w-72 px-3 py-3">
                            <div className="flex items-start gap-2">
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="-ml-2 size-7 shrink-0"
                                title={
                                  expandedRuleKey === rule.group_key
                                    ? "收起同类行为与 Memory 效果"
                                    : "查看同类行为与 Memory 效果"
                                }
                                aria-label={
                                  expandedRuleKey === rule.group_key
                                    ? "收起规则详情"
                                    : "展开规则详情"
                                }
                                aria-expanded={
                                  expandedRuleKey === rule.group_key
                                }
                                onClick={() =>
                                  setExpandedRuleKey((current) =>
                                    current === rule.group_key
                                      ? null
                                      : rule.group_key,
                                  )
                                }
                              >
                                {expandedRuleKey === rule.group_key ? (
                                  <ChevronDownIcon className="size-4" />
                                ) : (
                                  <ChevronRightIcon className="size-4" />
                                )}
                              </Button>
                              <div className="min-w-0">
                                <div className="font-medium break-words">
                                  {rule.rule_name ?? rule.detection_identity}
                                </div>
                                <div className="text-muted-foreground mt-1 font-mono break-all">
                                  {rule.rule_code ??
                                    rule.detection_key ??
                                    "unclassified"}
                                </div>
                                <div className="text-muted-foreground mt-1">
                                  {rule.source_type}
                                  {rule.source_system
                                    ? ` · ${rule.source_system}`
                                    : ""}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-3 tabular-nums">
                            {rule.alert_count}
                          </td>
                          <td className="px-3 py-3 tabular-nums">
                            {rule.labeled_count}/{rule.completed_count}
                            <div className="text-muted-foreground mt-1 text-xs">
                              {formatOptionalRate(rule.label_coverage)}{" "}
                              已有最终结果
                            </div>
                          </td>
                          <td className="px-3 py-3 tabular-nums">
                            {formatOptionalRate(rule.confirmed_risk_rate)}
                            <div className="text-muted-foreground mt-1">
                              {rule.final_risk_count}/{rule.labeled_count}
                            </div>
                          </td>
                          <td className="px-3 py-3 tabular-nums">
                            {formatOptionalRate(rule.false_positive_rate)}
                            <div className="text-muted-foreground mt-1">
                              {rule.final_false_positive_count}/
                              {rule.labeled_count}
                            </div>
                          </td>
                          <td className="px-3 py-3 tabular-nums">
                            准确 {formatOptionalRate(rule.triage_accuracy)}
                            <div className="text-muted-foreground mt-1">
                              漏报 {formatOptionalRate(rule.miss_rate)}
                            </div>
                          </td>
                          <td className="px-3 py-3 tabular-nums">
                            {formatOptionalRate(rule.auto_ignore_rate)}
                            {rule.wrong_auto_ignore_count > 0 ? (
                              <div className="mt-1 text-red-700">
                                错误 {rule.wrong_auto_ignore_count}
                              </div>
                            ) : null}
                          </td>
                          <td className="px-3 py-3 tabular-nums">
                            {compactNumber(rule.total_tokens)}
                            <div className="text-muted-foreground mt-1">
                              {rule.provider_call_count} calls
                            </div>
                          </td>
                          <td className="px-3 py-3 tabular-nums">
                            仅供参考 {rule.memory_context_use_count}
                            <div className="text-muted-foreground mt-1">
                              复用结论 {rule.memory_directive_use_count} ·
                              结论相反 {rule.memory_contradiction_count}
                            </div>
                          </td>
                          <td className="max-w-72 px-3 py-3">
                            <Badge
                              variant="outline"
                              className={cn(
                                "whitespace-normal",
                                RECOMMENDATION_CLASSES[
                                  rule.recommendation.priority
                                ],
                              )}
                            >
                              {rule.recommendation.title}
                            </Badge>
                            <p className="text-muted-foreground mt-2 line-clamp-3 leading-5">
                              {rule.recommendation.suggested_next_step}
                            </p>
                          </td>
                        </tr>
                        {expandedRuleKey === rule.group_key ? (
                          <tr>
                            <td
                              colSpan={10}
                              className="bg-muted/25 border-t px-4 py-4"
                            >
                              <RuleEffectivenessDrilldown
                                detail={ruleDetailQuery.detail}
                                isLoading={ruleDetailQuery.isLoading}
                                error={ruleDetailQuery.error}
                              />
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
                {snapshot.rules.length === 0 ? (
                  <div className="text-muted-foreground px-4 py-8 text-center text-sm">
                    当前窗口没有可聚合的告警 Run。
                  </div>
                ) : null}
              </div>
            </div>
          </div>

          <div className="flex items-start gap-2 border-t bg-emerald-50 px-5 py-3 text-xs leading-5 text-emerald-950 md:px-7">
            <ShieldCheckIcon className="mt-0.5 size-4 shrink-0" />
            <span>
              快速路径只针对“精确行为 + 已审核 Memory/Policy +
              无反证”的稳定模式，并保留抽样复核；绝不因同一 Rule Code 直接跳过
              LLM。
            </span>
          </div>
        </>
      ) : null}
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
  const [effectivenessWindowDays, setEffectivenessWindowDays] = useState(30);
  const query = useSocOperationsSnapshot();
  const effectivenessQuery = useSocEffectivenessSnapshot(
    effectivenessWindowDays,
  );
  const snapshot = query.snapshot;
  const metrics = snapshot?.persisted.metrics ?? null;
  const runStatuses = Object.entries(
    metrics?.analysis_run_status_counts ?? {},
  ).sort(([left], [right]) => left.localeCompare(right));

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={ActivityIcon}
        title="SOC 运营总览"
        description="运行状态、处理负载与治理信号"
        actions={
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() =>
              void Promise.all([query.refetch(), effectivenessQuery.refetch()])
            }
            disabled={query.isFetching || effectivenessQuery.isFetching}
            aria-label="刷新运营快照"
            title="刷新运营快照"
          >
            <RefreshCwIcon
              className={cn(
                "size-4",
                (query.isFetching || effectivenessQuery.isFetching) &&
                  "animate-spin",
              )}
            />
          </Button>
        }
      />

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

        {snapshot ? <DataNature snapshot={snapshot} /> : null}

        <EffectivenessSection
          snapshot={effectivenessQuery.snapshot}
          isLoading={effectivenessQuery.isLoading}
          error={effectivenessQuery.error}
          windowDays={effectivenessWindowDays}
          onWindowChange={setEffectivenessWindowDays}
        />

        {snapshot ? (
          <>
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
