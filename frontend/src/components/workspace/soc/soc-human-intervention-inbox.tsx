"use client";

import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  FileJsonIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
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
  useSocReviewContext,
  useSocReviewItems,
} from "@/core/soc";
import type {
  SocReviewQueueItem,
  SocReviewQueueStatus,
  SocVerdict,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const VERDICT_OPTIONS: Array<{ value: SocVerdict; label: string }> = [
  { value: "true_positive", label: "真实攻击" },
  { value: "false_positive", label: "误报 / 无风险" },
  { value: "suspicious", label: "可疑" },
  { value: "unknown", label: "暂无法判断" },
];

const VERDICT_LABELS: Record<SocVerdict, string> = {
  true_positive: "真实攻击",
  false_positive: "误报 / 无风险",
  suspicious: "可疑",
  unknown: "暂无法判断",
  needs_review: "需要进一步确认",
};

const REASON_LABELS: Record<string, string> = {
  fact_conflict: "关键事实冲突",
};

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

function asTextList(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function editableVerdict(value?: SocVerdict | null): SocVerdict {
  return value && value !== "needs_review" ? value : "suspicious";
}

function itemTitle(item: SocReviewQueueItem) {
  return (
    [item.rule_name, item.rule_code, item.category].find((value) =>
      value?.trim(),
    ) ?? `告警 ${item.alert_id}`
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
}: {
  title: string;
  items: string[];
  empty: string;
}) {
  return (
    <section>
      <h3 className="text-sm font-semibold">{title}</h3>
      {items.length ? (
        <ol className="mt-3 space-y-2 text-sm leading-6">
          {items.map((item, index) => (
            <li key={`${index}-${item}`} className="flex gap-2">
              <span className="text-muted-foreground shrink-0">
                {index + 1}.
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-muted-foreground mt-3 text-sm">{empty}</p>
      )}
    </section>
  );
}

export function SocHumanInterventionInbox({
  initialQueueId,
}: {
  initialQueueId?: string;
}) {
  const [status, setStatus] = useState<SocReviewQueueStatus | "all">("open");
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(
    initialQueueId ?? null,
  );
  const [verdict, setVerdict] = useState<SocVerdict>("suspicious");
  const [reason, setReason] = useState("");

  const inbox = useSocReviewItems({
    status: status === "all" ? null : status,
    limit: 100,
    humanInterventionOnly: true,
  });
  const items = useMemo(() => inbox.items, [inbox.items]);
  const selected =
    items.find((item) => item.queue_id === selectedQueueId) ?? items[0] ?? null;
  const activeQueueId = selected?.queue_id ?? null;
  const contextQuery = useSocReviewContext(activeQueueId);
  const context = contextQuery.context;
  const correction = useCorrectSocReviewRun();
  const analysis = asRecord(context?.run.analysis);
  const evidenceGaps = asTextList(analysis.evidence_gaps);
  const manualChecks = asTextList(analysis.manual_checks);
  const conflictReports = asRecord(
    context?.run.fact_reconstruction,
  ).conflict_reports;

  useEffect(() => {
    if (!selectedQueueId && items[0]?.queue_id) {
      setSelectedQueueId(items[0].queue_id);
      return;
    }
    if (
      selectedQueueId &&
      !items.some((item) => item.queue_id === selectedQueueId)
    ) {
      setSelectedQueueId(items[0]?.queue_id ?? null);
    }
  }, [items, selectedQueueId]);

  useEffect(() => {
    setVerdict(editableVerdict(selected?.verdict));
    setReason("");
  }, [selected?.queue_id, selected?.verdict]);

  const submit = async () => {
    if (!selected?.run_id || !reason.trim()) return;
    try {
      await correction.mutateAsync({
        runId: selected.run_id,
        request: { verdict, reason: reason.trim() },
      });
      toast.success("最终判断已记录，人工介入任务已完成");
      setReason("");
      await inbox.refetch();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交失败");
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={AlertTriangleIcon}
        title="需人工介入"
        description="这里只处理 Runtime 无法自动裁决的关键事实冲突；普通证据提示仍留在告警研判结果中"
        actions={
          <>
            <Select
              value={status}
              onValueChange={(value) =>
                setStatus(value as SocReviewQueueStatus | "all")
              }
            >
              <SelectTrigger className="w-32" aria-label="人工介入状态">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="open">待处理</SelectItem>
                <SelectItem value="closed">已完成</SelectItem>
                <SelectItem value="all">全部</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="icon"
              aria-label="刷新人工介入任务"
              title="刷新人工介入任务"
              onClick={() => void inbox.refetch()}
              disabled={inbox.isFetching}
            >
              <RefreshCwIcon
                className={cn("size-4", inbox.isFetching && "animate-spin")}
              />
            </Button>
          </>
        }
      />

      <div className="grid min-h-0 flex-1 lg:grid-cols-[22rem_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-y-auto border-b p-2 lg:border-r lg:border-b-0">
          {inbox.isLoading ? (
            <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
              正在加载人工介入任务...
            </div>
          ) : inbox.error ? (
            <div className="text-destructive p-4 text-center text-sm">
              {inbox.error instanceof Error ? inbox.error.message : "加载失败"}
            </div>
          ) : items.length === 0 ? (
            <div className="text-muted-foreground flex h-40 flex-col items-center justify-center gap-2 px-6 text-center text-sm">
              <CheckCircle2Icon className="size-6 text-emerald-600" />
              <span>当前没有需要人工裁决的关键冲突</span>
            </div>
          ) : (
            <div className="space-y-1">
              {items.map((item) => (
                <button
                  key={item.queue_id}
                  type="button"
                  onClick={() => setSelectedQueueId(item.queue_id)}
                  className={cn(
                    "hover:bg-accent focus-visible:ring-ring w-full border-l-2 border-transparent px-3 py-3 text-left focus-visible:ring-2 focus-visible:outline-none",
                    item.queue_id === activeQueueId &&
                      "bg-accent border-l-amber-500",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 truncate text-sm font-medium">
                      {itemTitle(item)}
                    </div>
                    <Badge variant="outline" className="shrink-0">
                      {item.status === "open" ? "待处理" : "已完成"}
                    </Badge>
                  </div>
                  <div className="text-muted-foreground mt-1 truncate font-mono text-xs">
                    {item.alert_id}
                  </div>
                  <div className="text-muted-foreground mt-2 flex justify-between gap-2 text-xs">
                    <span>{REASON_LABELS[item.reason] ?? item.reason}</span>
                    <span>{formatTime(item.updated_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </aside>

        <main className="min-h-0 overflow-y-auto">
          {!selected ? (
            <div className="text-muted-foreground flex min-h-96 items-center justify-center text-sm">
              选择一条任务查看关键冲突
            </div>
          ) : contextQuery.isLoading ? (
            <div className="text-muted-foreground flex min-h-96 items-center justify-center text-sm">
              正在加载调查上下文...
            </div>
          ) : contextQuery.error ? (
            <div className="text-destructive flex min-h-96 items-center justify-center p-8 text-sm">
              {contextQuery.error instanceof Error
                ? contextQuery.error.message
                : "调查上下文加载失败"}
            </div>
          ) : (
            <div className="mx-auto flex max-w-6xl flex-col gap-5 p-5 md:p-7">
              <section className="border">
                <div className="flex flex-wrap items-start justify-between gap-4 border-b p-5">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-semibold">
                        {itemTitle(selected)}
                      </h2>
                      <Badge
                        className="border-amber-300 bg-amber-50 text-amber-800"
                        variant="outline"
                      >
                        关键事实冲突
                      </Badge>
                    </div>
                    <p className="text-muted-foreground mt-1 font-mono text-xs">
                      {selected.alert_id} / {selected.run_id}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button asChild size="sm" variant="outline">
                      <Link
                        href={`/workspace/soc/alerts?run_id=${encodeURIComponent(selected.run_id)}`}
                      >
                        <ShieldAlertIcon className="size-4" />
                        返回完整研判
                      </Link>
                    </Button>
                    {selected.status === "open" ? (
                      <Button asChild size="sm" variant="outline">
                        <Link
                          href={`/workspace/agents/soc-triage/chats/new?queue_id=${encodeURIComponent(selected.queue_id)}`}
                        >
                          <BotIcon className="size-4" />
                          交给 Lead Agent 调查
                        </Link>
                      </Button>
                    ) : null}
                  </div>
                </div>
                <div className="grid divide-y lg:grid-cols-[1.2fr_0.8fr] lg:divide-x lg:divide-y-0">
                  <div className="p-5">
                    <div className="text-muted-foreground text-xs font-medium">
                      系统当前结论
                    </div>
                    <p className="mt-3 text-base leading-7 font-medium">
                      {selected.summary ?? "系统未提供结论摘要。"}
                    </p>
                  </div>
                  <dl className="p-5">
                    <DetailRow
                      label="当前判断"
                      value={
                        selected.verdict
                          ? VERDICT_LABELS[selected.verdict]
                          : "无结论"
                      }
                    />
                    <DetailRow
                      label="置信度"
                      value={formatPercent(selected.confidence)}
                    />
                    <DetailRow
                      label="检测规则"
                      value={`${selected.rule_code ?? "-"} / ${selected.rule_name ?? "-"}`}
                    />
                    <DetailRow
                      label="告警来源"
                      value={`${selected.source_type}${selected.source_system ? ` / ${selected.source_system}` : ""}`}
                    />
                  </dl>
                </div>
              </section>

              <section className="grid gap-5 border p-5 lg:grid-cols-2">
                <GuidanceList
                  title="仍缺少的证据"
                  items={evidenceGaps}
                  empty="没有额外证据缺口；本任务由关键事实冲突触发。"
                />
                <GuidanceList
                  title="建议核查"
                  items={manualChecks}
                  empty="请直接核对冲突事实并记录最终判断。"
                />
              </section>

              <section className="border border-l-4 border-l-amber-500">
                <div className="border-b p-5">
                  <h3 className="text-sm font-semibold">
                    {selected.status === "open"
                      ? "记录最终判断"
                      : "人工介入结果"}
                  </h3>
                  <p className="text-muted-foreground mt-1 text-sm">
                    这里只解决当前告警的事实冲突，不会自动生成或审核经验，也不会授权外部动作。
                  </p>
                </div>
                {selected.status === "open" ? (
                  <div className="grid gap-4 p-5 lg:grid-cols-[15rem_minmax(0,1fr)_auto] lg:items-end">
                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="intervention-verdict"
                      >
                        最终判断
                      </label>
                      <Select
                        value={verdict}
                        onValueChange={(value) =>
                          setVerdict(value as SocVerdict)
                        }
                      >
                        <SelectTrigger
                          id="intervention-verdict"
                          aria-label="最终判断"
                        >
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
                    </div>
                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="intervention-reason"
                      >
                        判断依据
                      </label>
                      <Textarea
                        id="intervention-reason"
                        aria-label="最终判断依据"
                        placeholder="写明用于解决冲突的业务事实或调查结果"
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        className="min-h-20 resize-none"
                      />
                    </div>
                    <Button
                      onClick={() => void submit()}
                      disabled={correction.isPending || !reason.trim()}
                    >
                      <CheckCircle2Icon className="size-4" />
                      提交并完成介入
                    </Button>
                  </div>
                ) : (
                  <dl className="p-5">
                    <DetailRow
                      label="处理人"
                      value={selected.closed_by?.actor_id ?? "-"}
                    />
                    <DetailRow
                      label="完成时间"
                      value={formatTime(selected.closed_at)}
                    />
                    <DetailRow
                      label="处理依据"
                      value={selected.close_reason ?? "-"}
                    />
                  </dl>
                )}
              </section>

              <Collapsible>
                <section className="border">
                  <CollapsibleTrigger asChild>
                    <Button
                      variant="ghost"
                      className="group h-auto w-full justify-between rounded-none px-5 py-4"
                    >
                      <span className="flex items-center gap-2 text-sm font-semibold">
                        <FileJsonIcon className="text-muted-foreground size-4" />
                        冲突报告与技术审计
                      </span>
                      <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <pre className="bg-muted/40 max-h-[32rem] overflow-auto border-t p-5 text-xs whitespace-pre-wrap">
                      {JSON.stringify(
                        {
                          conflict_reports: conflictReports ?? [],
                          review_reasons: selected.review_reasons ?? [],
                          investigation_evidence:
                            context?.action_evidence ?? [],
                          decision_audit: context?.audit_records ?? [],
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </CollapsibleContent>
                </section>
              </Collapsible>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
