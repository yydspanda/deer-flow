"use client";

import {
  AlertCircleIcon,
  AlertTriangleIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ClipboardCheckIcon,
  FileJsonIcon,
  FileSearchIcon,
  HistoryIcon,
  PencilLineIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldAlertIcon,
  SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
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
import { Textarea } from "@/components/ui/textarea";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useCorrectSocReviewRun,
  usePromoteSocRunToMemory,
  useSocAlertInvestigationContext,
  useSocAlertResults,
} from "@/core/soc";
import type {
  SocAlertAttentionLevel,
  SocAlertResult,
  SocMemoryCandidate,
  SocVerdict,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const VERDICT_LABELS: Record<SocVerdict, string> = {
  true_positive: "真实攻击",
  false_positive: "误报 / 无风险",
  suspicious: "可疑",
  unknown: "暂无法判断",
  needs_review: "需要进一步确认",
};

const ATTENTION_LABELS: Record<SocAlertAttentionLevel, string> = {
  none: "结论可用",
  advisory: "存在提示",
  required: "需人工介入",
};

const VERDICT_OPTIONS: Array<{ value: SocVerdict; label: string }> = [
  { value: "true_positive", label: "真实攻击" },
  { value: "false_positive", label: "误报 / 无风险" },
  { value: "suspicious", label: "可疑" },
  { value: "unknown", label: "暂无法判断" },
];

function formatTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatPercent(value?: number | null) {
  return value == null ? "-" : `${Math.round(value * 100)}%`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asText(value: unknown, fallback = "-") {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function asTextList(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function verdictClass(verdict?: SocVerdict | null) {
  if (verdict === "true_positive") {
    return "border-red-300 bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300";
  }
  if (verdict === "false_positive") {
    return "border-emerald-300 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300";
  }
  if (verdict === "suspicious") {
    return "border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300";
  }
  return "border-border bg-muted text-muted-foreground";
}

function attentionClass(level: SocAlertAttentionLevel) {
  if (level === "required") {
    return "border-red-300 bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300";
  }
  if (level === "advisory") {
    return "border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300";
  }
  return "border-emerald-300 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300";
}

function resultTitle(item: SocAlertResult) {
  return (
    [
      item.summary.rule_name,
      item.summary.rule_code,
      item.summary.category,
    ].find((value) => value?.trim()) ?? `告警 ${item.summary.alert_id}`
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 border-b py-2 text-sm last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

function GuidanceList({
  title,
  items,
  empty,
  warning = false,
}: {
  title: string;
  items: string[];
  empty: string;
  warning?: boolean;
}) {
  return (
    <section className="border-t pt-4 first:border-t-0">
      <div className="flex items-center gap-2 text-sm font-semibold">
        {warning ? (
          <AlertTriangleIcon className="size-4 text-amber-600" />
        ) : (
          <ClipboardCheckIcon className="text-muted-foreground size-4" />
        )}
        {title}
      </div>
      {items.length ? (
        <ul className="mt-3 space-y-2 text-sm leading-6">
          {items.map((item, index) => (
            <li key={`${index}-${item}`} className="flex gap-2">
              <span className="text-muted-foreground mt-0.5 shrink-0">
                {index + 1}.
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground mt-3 text-sm">{empty}</p>
      )}
    </section>
  );
}

function candidateAction(candidate: SocMemoryCandidate | null) {
  if (!candidate) return null;
  if (candidate.status === "pending_review") return "查看待审核经验";
  if (candidate.status === "confirmed") return "查看已确认经验";
  if (candidate.status === "rejected") return "查看未沉淀记录";
  return "查看经验状态";
}

export function SocAlertResultsWorkbench({
  initialRunId,
}: {
  initialRunId?: string;
}) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    initialRunId ?? null,
  );
  const [search, setSearch] = useState("");
  const [verdictFilter, setVerdictFilter] = useState<SocVerdict | "all">("all");
  const [attentionFilter, setAttentionFilter] = useState<
    SocAlertAttentionLevel | "all"
  >("all");
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [correctionVerdict, setCorrectionVerdict] =
    useState<SocVerdict>("suspicious");
  const [correctionReason, setCorrectionReason] = useState("");
  const [promotionOpen, setPromotionOpen] = useState(false);
  const [promotionReason, setPromotionReason] = useState("");

  const resultsQuery = useSocAlertResults({ limit: 200 });
  const results = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return resultsQuery.results.filter((item) => {
      if (verdictFilter !== "all" && item.summary.verdict !== verdictFilter) {
        return false;
      }
      if (
        attentionFilter !== "all" &&
        item.attention_level !== attentionFilter
      ) {
        return false;
      }
      if (!query) return true;
      return [
        item.summary.alert_id,
        item.summary.run_id,
        item.summary.rule_code,
        item.summary.rule_name,
        item.summary.source_system,
        item.summary.summary,
      ].some((value) => value?.toLocaleLowerCase().includes(query));
    });
  }, [attentionFilter, resultsQuery.results, search, verdictFilter]);

  useEffect(() => {
    if (
      selectedRunId &&
      results.some((item) => item.summary.run_id === selectedRunId)
    ) {
      return;
    }
    setSelectedRunId(results[0]?.summary.run_id ?? null);
  }, [results, selectedRunId]);

  const contextQuery = useSocAlertInvestigationContext(selectedRunId);
  const context = contextQuery.context;
  const selectedResult =
    context?.result ??
    results.find((item) => item.summary.run_id === selectedRunId) ??
    null;
  const analysis = asRecord(context?.run.analysis);
  const decision = asRecord(context?.run.decision);
  const evidenceGaps = asTextList(analysis.evidence_gaps);
  const manualChecks = asTextList(analysis.manual_checks);
  const scenarios = Array.isArray(analysis.scenario_assessments)
    ? analysis.scenario_assessments.map(asRecord)
    : [];
  const primaryScenario = scenarios.find((item) => item.is_primary === true);
  const memoryCandidate = context?.memory_candidates[0] ?? null;
  const correctMutation = useCorrectSocReviewRun();
  const promoteMutation = usePromoteSocRunToMemory();

  useEffect(() => {
    if (selectedResult?.summary.verdict) {
      setCorrectionVerdict(selectedResult.summary.verdict);
    }
  }, [selectedResult?.summary.verdict]);

  const submitCorrection = async () => {
    if (!selectedRunId || !correctionReason.trim()) return;
    try {
      await correctMutation.mutateAsync({
        runId: selectedRunId,
        request: {
          verdict: correctionVerdict,
          reason: correctionReason.trim(),
        },
      });
      setCorrectionOpen(false);
      setCorrectionReason("");
      toast.success("研判结论已修正并保留审计记录");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "修正失败");
    }
  };

  const submitPromotion = async () => {
    if (!selectedRunId) return;
    try {
      const result = await promoteMutation.mutateAsync({
        runId: selectedRunId,
        request: { note: promotionReason.trim() || undefined },
      });
      setPromotionOpen(false);
      setPromotionReason("");
      if (result.memory_candidate) {
        toast.success("已进入经验审核，确认前不会影响新告警");
      } else {
        toast.info("已记录本次观察，尚未形成待审核经验");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提炼失败");
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={ShieldAlertIcon}
        title="告警研判"
        description="查看每条告警的当前结论；只有关键事实冲突才生成需人工介入任务"
        actions={
          <Button
            variant="outline"
            size="icon"
            title="刷新告警结果"
            aria-label="刷新告警结果"
            onClick={() => void resultsQuery.refetch()}
            disabled={resultsQuery.isFetching}
          >
            <RefreshCwIcon
              className={cn(
                "size-4",
                resultsQuery.isFetching && "animate-spin",
              )}
            />
          </Button>
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[23rem_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col border-b lg:border-r lg:border-b-0">
          <div className="space-y-3 border-b p-4">
            <div className="relative">
              <SearchIcon className="text-muted-foreground absolute top-2.5 left-3 size-4" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索告警、规则或来源"
                className="pl-9"
                aria-label="搜索告警结果"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Select
                value={verdictFilter}
                onValueChange={(value) =>
                  setVerdictFilter(value as SocVerdict | "all")
                }
              >
                <SelectTrigger aria-label="按结论筛选">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部结论</SelectItem>
                  {VERDICT_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={attentionFilter}
                onValueChange={(value) =>
                  setAttentionFilter(value as SocAlertAttentionLevel | "all")
                }
              >
                <SelectTrigger aria-label="按关注级别筛选">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部状态</SelectItem>
                  <SelectItem value="none">结论可用</SelectItem>
                  <SelectItem value="advisory">存在提示</SelectItem>
                  <SelectItem value="required">需人工介入</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="text-muted-foreground flex items-center justify-between text-xs">
              <span>{results.length} 条结果</span>
              <span>
                {
                  results.filter((item) => item.requires_human_intervention)
                    .length
                }{" "}
                条需介入
              </span>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {resultsQuery.isLoading ? (
              <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
                正在读取告警结果...
              </div>
            ) : resultsQuery.error ? (
              <div className="text-destructive p-4 text-center text-sm">
                {resultsQuery.error instanceof Error
                  ? resultsQuery.error.message
                  : "告警结果加载失败"}
              </div>
            ) : results.length === 0 ? (
              <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
                没有符合条件的告警
              </div>
            ) : (
              <div className="space-y-1">
                {results.map((item) => {
                  const active = item.summary.run_id === selectedRunId;
                  return (
                    <button
                      key={item.summary.run_id}
                      type="button"
                      onClick={() => setSelectedRunId(item.summary.run_id)}
                      className={cn(
                        "hover:bg-accent focus-visible:ring-ring w-full border-l-2 border-transparent px-3 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none",
                        active && "bg-accent border-l-foreground",
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium">
                            {resultTitle(item)}
                          </div>
                          <div className="text-muted-foreground mt-1 truncate font-mono text-xs">
                            {item.summary.alert_id}
                          </div>
                        </div>
                        <Badge
                          variant="outline"
                          className={cn(
                            "shrink-0",
                            verdictClass(item.summary.verdict),
                          )}
                        >
                          {item.summary.verdict
                            ? VERDICT_LABELS[item.summary.verdict]
                            : "无结论"}
                        </Badge>
                      </div>
                      <div className="mt-2 flex items-center justify-between gap-2 text-xs">
                        <span
                          className={cn(
                            "font-medium",
                            item.attention_level === "required" &&
                              "text-red-600",
                            item.attention_level === "advisory" &&
                              "text-amber-700",
                            item.attention_level === "none" &&
                              "text-emerald-700",
                          )}
                        >
                          {ATTENTION_LABELS[item.attention_level]}
                        </span>
                        <span className="text-muted-foreground">
                          {formatTime(item.summary.updated_at)}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        <main className="min-h-0 overflow-y-auto">
          {!selectedResult ? (
            <div className="text-muted-foreground flex min-h-96 items-center justify-center text-sm">
              选择一条告警查看研判结果
            </div>
          ) : contextQuery.isLoading ? (
            <div className="text-muted-foreground flex min-h-96 items-center justify-center text-sm">
              正在加载完整研判过程...
            </div>
          ) : contextQuery.error ? (
            <div className="text-destructive flex min-h-96 items-center justify-center p-8 text-sm">
              {contextQuery.error instanceof Error
                ? contextQuery.error.message
                : "研判详情加载失败"}
            </div>
          ) : (
            <div className="mx-auto flex max-w-7xl flex-col gap-5 p-5 md:p-7">
              <section className="overflow-hidden border">
                <div className="flex flex-wrap items-start justify-between gap-4 border-b p-5">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-semibold">
                        {resultTitle(selectedResult)}
                      </h2>
                      <Badge
                        variant="outline"
                        className={verdictClass(selectedResult.summary.verdict)}
                      >
                        {selectedResult.summary.verdict
                          ? VERDICT_LABELS[selectedResult.summary.verdict]
                          : "无结论"}
                      </Badge>
                      <Badge
                        variant="outline"
                        className={attentionClass(
                          selectedResult.attention_level,
                        )}
                      >
                        {ATTENTION_LABELS[selectedResult.attention_level]}
                      </Badge>
                    </div>
                    <p className="text-muted-foreground mt-1 font-mono text-xs">
                      {selectedResult.summary.alert_id} /{" "}
                      {selectedResult.summary.run_id}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {selectedResult.queue_item ? (
                      <Button asChild variant="outline" size="sm">
                        <Link
                          href={`/workspace/soc/review/alerts?queue_id=${encodeURIComponent(selectedResult.queue_item.queue_id)}`}
                        >
                          <AlertCircleIcon className="size-4" />
                          处理人工介入
                        </Link>
                      </Button>
                    ) : null}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setCorrectionOpen(true)}
                    >
                      <PencilLineIcon className="size-4" />
                      修正结论
                    </Button>
                    {memoryCandidate ? (
                      <Button asChild size="sm">
                        <Link
                          href={`/workspace/soc/review/memory-candidates/${encodeURIComponent(memoryCandidate.candidate_id)}`}
                        >
                          <BrainCircuitIcon className="size-4" />
                          {candidateAction(memoryCandidate)}
                        </Link>
                      </Button>
                    ) : (
                      <Button size="sm" onClick={() => setPromotionOpen(true)}>
                        <SparklesIcon className="size-4" />
                        提炼为经验
                      </Button>
                    )}
                  </div>
                </div>

                <div className="grid divide-y lg:grid-cols-[1.3fr_0.7fr] lg:divide-x lg:divide-y-0">
                  <div className="p-5">
                    <div className="text-muted-foreground text-xs font-medium">
                      当前结论 / Current Decision
                    </div>
                    <p className="mt-3 text-base leading-7 font-medium">
                      {asText(
                        analysis.summary,
                        selectedResult.summary.summary ??
                          "系统未提供结论摘要。",
                      )}
                    </p>
                    <p className="text-muted-foreground mt-3 text-sm leading-6">
                      {asText(
                        decision.reason,
                        asText(analysis.reason, "暂无补充理由。"),
                      )}
                    </p>
                  </div>
                  <dl className="p-5">
                    <DetailRow
                      label="置信度"
                      value={formatPercent(selectedResult.summary.confidence)}
                    />
                    <DetailRow
                      label="主场景"
                      value={asText(
                        primaryScenario?.scenario_name,
                        selectedResult.summary.category ?? "-",
                      )}
                    />
                    <DetailRow
                      label="建议处置"
                      value={asText(
                        analysis.recommended_action,
                        selectedResult.summary.recommended_action ?? "-",
                      )}
                    />
                    <DetailRow
                      label="告警来源"
                      value={`${selectedResult.summary.source_type ?? "unknown"}${selectedResult.summary.source_system ? ` / ${selectedResult.summary.source_system}` : ""}`}
                    />
                    <DetailRow
                      label="检测规则"
                      value={`${selectedResult.summary.rule_code ?? "-"} / ${selectedResult.summary.rule_name ?? "-"}`}
                    />
                  </dl>
                </div>
              </section>

              {selectedResult.attention_level !== "none" ? (
                <section
                  className={cn(
                    "flex items-start gap-3 border-l-4 p-4",
                    selectedResult.attention_level === "required"
                      ? "border-red-500 bg-red-50/60 dark:bg-red-950/20"
                      : "border-amber-500 bg-amber-50/60 dark:bg-amber-950/20",
                  )}
                >
                  <AlertTriangleIcon className="mt-0.5 size-5 shrink-0" />
                  <div>
                    <div className="text-sm font-semibold">
                      {selectedResult.attention_level === "required"
                        ? "关键事实仍有冲突，需要人工决定"
                        : "当前结论可以查看，但存在质量或证据提示"}
                    </div>
                    <p className="mt-1 text-sm leading-6">
                      {selectedResult.attention_reasons.length
                        ? selectedResult.attention_reasons.join("、")
                        : "详情见下方证据缺口与技术记录。"}
                    </p>
                  </div>
                </section>
              ) : null}

              <section className="grid gap-6 border p-5 xl:grid-cols-2">
                <GuidanceList
                  title="证据缺口"
                  items={evidenceGaps}
                  empty="当前研判未报告关键证据缺口。"
                  warning
                />
                <GuidanceList
                  title="可选核查建议"
                  items={manualChecks}
                  empty="当前没有额外人工核查建议。"
                />
              </section>

              <section className="border">
                <div className="flex items-center justify-between gap-3 border-b p-4">
                  <div className="flex items-center gap-2">
                    <BrainCircuitIcon className="text-muted-foreground size-4" />
                    <h3 className="text-sm font-semibold">
                      历史经验如何参与本次研判
                    </h3>
                  </div>
                  <Badge variant="secondary">
                    {context?.relevant_memories?.returned_count ?? 0} 条
                  </Badge>
                </div>
                {context?.relevant_memories?.matches.length ? (
                  <div className="divide-y">
                    {context.relevant_memories.matches.map((match) => (
                      <div
                        key={match.memory_id}
                        className="grid gap-3 p-4 lg:grid-cols-[minmax(0,1fr)_12rem]"
                      >
                        <div>
                          <div className="text-sm font-medium">
                            {match.record.summary}
                          </div>
                          <p className="text-muted-foreground mt-1 line-clamp-3 text-sm leading-6">
                            {match.record.business_lesson?.conclusion ??
                              match.record.content}
                          </p>
                        </div>
                        <div className="text-muted-foreground text-xs lg:text-right">
                          <div>匹配分 {match.score.toFixed(1)}</div>
                          <div className="mt-1">
                            {match.anchor_match_reasons
                              .slice(0, 2)
                              .join(" / ") || "背景参考"}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-muted-foreground p-5 text-sm">
                    本次没有使用已确认经验；系统仍应根据当前告警证据给出结论。
                  </div>
                )}
              </section>

              <section className="grid gap-5 border p-5 lg:grid-cols-3">
                <div>
                  <div className="flex items-center gap-2 text-sm font-semibold">
                    <HistoryIcon className="text-muted-foreground size-4" />
                    处置与反馈
                  </div>
                  <p className="text-muted-foreground mt-2 text-sm leading-6">
                    {context?.external_dispositions.length ?? 0} 条外部反馈，
                    {context?.disposition_proposals.length ?? 0} 条处置建议，
                    {context?.disposition_outcomes.length ?? 0} 条处置结果。
                  </p>
                </div>
                <div>
                  <div className="flex items-center gap-2 text-sm font-semibold">
                    <FileSearchIcon className="text-muted-foreground size-4" />
                    调查补充
                  </div>
                  <p className="text-muted-foreground mt-2 text-sm leading-6">
                    {context?.action_evidence.length ?? 0} 条工具证据，
                    {context?.authorization_enrichments.length ?? 0}{" "}
                    条授权活动匹配，
                    {context?.similar_alerts.length ?? 0} 条相似告警。
                  </p>
                </div>
                <div>
                  <div className="flex items-center gap-2 text-sm font-semibold">
                    <BrainCircuitIcon className="text-muted-foreground size-4" />
                    经验沉淀
                  </div>
                  <p className="text-muted-foreground mt-2 text-sm leading-6">
                    {memoryCandidate
                      ? `已存在 ${memoryCandidate.status} Candidate，进入经验治理链路。`
                      : "当前尚未提炼经验；可由运营按价值选择是否沉淀。"}
                  </p>
                </div>
              </section>

              <Collapsible>
                <section className="border">
                  <CollapsibleTrigger asChild>
                    <button
                      type="button"
                      className="hover:bg-muted/50 flex w-full items-center justify-between gap-3 p-4 text-left"
                    >
                      <span className="flex items-center gap-2 text-sm font-semibold">
                        <FileJsonIcon className="text-muted-foreground size-4" />
                        技术详情与完整审计
                      </span>
                      <ChevronDownIcon className="text-muted-foreground size-4" />
                    </button>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <pre className="bg-muted/40 max-h-[42rem] overflow-auto border-t p-4 text-xs leading-5 whitespace-pre-wrap">
                      {JSON.stringify(context, null, 2)}
                    </pre>
                  </CollapsibleContent>
                </section>
              </Collapsible>
            </div>
          )}
        </main>
      </div>

      <Dialog open={correctionOpen} onOpenChange={setCorrectionOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修正研判结论</DialogTitle>
            <DialogDescription>
              原始模型结论保持不变；修正结果和理由会单独留痕。
            </DialogDescription>
          </DialogHeader>
          <Select
            value={correctionVerdict}
            onValueChange={(value) => setCorrectionVerdict(value as SocVerdict)}
          >
            <SelectTrigger aria-label="修正后的结论">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {VERDICT_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Textarea
            value={correctionReason}
            onChange={(event) => setCorrectionReason(event.target.value)}
            placeholder="填写支持修正结论的业务事实或调查依据"
            className="min-h-28 resize-none"
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setCorrectionOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => void submitCorrection()}
              disabled={correctMutation.isPending || !correctionReason.trim()}
            >
              <CheckCircle2Icon className="size-4" />
              确认修正
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={promotionOpen} onOpenChange={setPromotionOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>提炼为经验</DialogTitle>
            <DialogDescription>
              先生成待审核 Candidate；审核确认后才会成为可复用 Memory。
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={promotionReason}
            onChange={(event) => setPromotionReason(event.target.value)}
            placeholder="可选：说明这条告警为什么值得沉淀"
            className="min-h-28 resize-none"
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setPromotionOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => void submitPromotion()}
              disabled={promoteMutation.isPending}
            >
              <SparklesIcon className="size-4" />
              进入经验审核
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
