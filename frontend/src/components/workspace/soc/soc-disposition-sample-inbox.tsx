"use client";

import {
  CheckCircle2Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClipboardCheckIcon,
  Clock3Icon,
  InboxIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useSocDispositionSampleCampaigns,
  useSocDispositionSampleReviewInbox,
} from "@/core/soc";
import type {
  SocDispositionSampleReviewItem,
  SocDispositionSampleReviewReadiness,
  SocReviewQueueItem,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;

export interface SocDispositionSampleReviewTarget {
  sampleId: string;
  proposalId: string;
  queueItem: SocReviewQueueItem;
  canRecordOutcome: boolean;
}

function formatTime(value: string | null | undefined) {
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

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function readinessLabel(readiness: SocDispositionSampleReviewReadiness) {
  switch (readiness) {
    case "ready":
      return "待抽样复核";
    case "waiting_for_queue_close":
      return "等待主工单关闭";
    case "completed":
      return "已完成";
    case "unavailable":
      return "数据不可用";
  }
}

function readinessIcon(readiness: SocDispositionSampleReviewReadiness) {
  if (readiness === "completed") {
    return <CheckCircle2Icon className="size-4 text-emerald-600" />;
  }
  if (readiness === "waiting_for_queue_close") {
    return <Clock3Icon className="size-4 text-amber-600" />;
  }
  if (readiness === "unavailable") {
    return <ShieldAlertIcon className="text-destructive size-4" />;
  }
  return <ClipboardCheckIcon className="size-4 text-sky-600" />;
}

function readinessBadge(readiness: SocDispositionSampleReviewReadiness) {
  if (readiness === "completed") return "border-emerald-300 text-emerald-700";
  if (readiness === "waiting_for_queue_close") {
    return "border-amber-300 text-amber-700";
  }
  if (readiness === "unavailable") {
    return "border-destructive/40 text-destructive";
  }
  return "border-sky-300 text-sky-700";
}

function SampleReviewItem({
  item,
  onOpenReview,
}: {
  item: SocDispositionSampleReviewItem;
  onOpenReview: (target: SocDispositionSampleReviewTarget) => void;
}) {
  const queueItem = item.queue_item;
  return (
    <div className="grid gap-3 border-b p-4 last:border-b-0 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {readinessIcon(item.readiness)}
          <span className="font-mono text-sm font-medium">
            #{item.selection_rank} {item.proposal_id}
          </span>
          <Badge variant="outline" className={readinessBadge(item.readiness)}>
            {readinessLabel(item.readiness)}
          </Badge>
          {!item.reviewer_independent ? (
            <Badge variant="destructive">非独立 reviewer</Badge>
          ) : null}
        </div>
        <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <span>Queue {queueItem?.queue_id ?? "-"}</span>
          <span>
            Alert {queueItem?.alert_id ?? item.proposal?.alert_id ?? "-"}
          </span>
          <span>
            Proposal {item.proposal?.proposed_disposition ?? "unavailable"}
          </span>
          <span>
            Primary {item.primary_outcome?.observed_disposition ?? "pending"}
          </span>
          <span>
            Sample {item.sampled_outcome?.observed_disposition ?? "pending"}
          </span>
        </div>
        {item.blocking_reasons.length > 0 ? (
          <div className="text-muted-foreground text-xs">
            {item.blocking_reasons.join("; ")}
          </div>
        ) : null}
      </div>
      <Button
        size="sm"
        variant={item.can_record_outcome ? "default" : "outline"}
        disabled={!queueItem}
        onClick={() =>
          queueItem &&
          onOpenReview({
            sampleId: item.sample_id,
            proposalId: item.proposal_id,
            queueItem,
            canRecordOutcome: item.can_record_outcome,
          })
        }
      >
        <ClipboardCheckIcon className="size-4" />
        {item.can_record_outcome ? "打开复核" : "查看工单"}
      </Button>
    </div>
  );
}

export function SocDispositionSampleInbox({
  onOpenReview,
}: {
  onOpenReview: (target: SocDispositionSampleReviewTarget) => void;
}) {
  const [selectedSampleId, setSelectedSampleId] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const {
    campaigns,
    hasMore: hasMoreCampaigns,
    isLoading: campaignsLoading,
    isFetching: campaignsFetching,
    error: campaignsError,
    refetch: refetchCampaigns,
  } = useSocDispositionSampleCampaigns({ limit: 50 });
  const activeCampaign =
    campaigns.find((campaign) => campaign.sample_id === selectedSampleId) ??
    campaigns[0] ??
    null;
  const {
    inbox,
    isLoading: inboxLoading,
    isFetching: inboxFetching,
    error: inboxError,
    refetch: refetchInbox,
  } = useSocDispositionSampleReviewInbox(activeCampaign?.sample_id, {
    offset,
    limit: PAGE_SIZE,
  });

  useEffect(() => {
    if (activeCampaign && activeCampaign.sample_id !== selectedSampleId) {
      setSelectedSampleId(activeCampaign.sample_id);
    }
  }, [activeCampaign, selectedSampleId]);

  const selectCampaign = (sampleId: string) => {
    setSelectedSampleId(sampleId);
    setOffset(0);
  };

  const refresh = () => {
    void refetchCampaigns();
    if (activeCampaign) void refetchInbox();
  };

  if (campaignsLoading) {
    return (
      <div className="text-muted-foreground flex size-full items-center justify-center text-sm">
        加载抽样批次...
      </div>
    );
  }

  if (campaignsError) {
    return (
      <div className="text-destructive flex size-full items-center justify-center p-6 text-sm">
        {campaignsError instanceof Error
          ? campaignsError.message
          : "抽样批次加载失败"}
      </div>
    );
  }

  if (campaigns.length === 0) {
    return (
      <div className="flex size-full flex-col items-center justify-center gap-3 text-center">
        <InboxIcon className="text-muted-foreground size-8" />
        <p className="text-sm font-medium">暂无抽样复核批次</p>
      </div>
    );
  }

  return (
    <div className="grid size-full min-h-0 grid-cols-1 lg:grid-cols-[20rem_minmax(0,1fr)]">
      <aside className="min-h-0 border-r">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="text-sm font-medium">抽样批次</div>
          <Badge variant="secondary">{campaigns.length}</Badge>
        </div>
        <div className="h-full overflow-y-auto p-3">
          <div className="space-y-2">
            {campaigns.map((campaign) => (
              <button
                key={campaign.sample_id}
                type="button"
                className={cn(
                  "hover:bg-muted/60 w-full rounded-md border p-3 text-left transition-colors",
                  activeCampaign?.sample_id === campaign.sample_id &&
                    "border-foreground/30 bg-muted",
                )}
                onClick={() => selectCampaign(campaign.sample_id)}
              >
                <div className="truncate font-mono text-xs font-medium">
                  {campaign.sample_id}
                </div>
                <div className="text-muted-foreground mt-2 flex items-center justify-between text-xs">
                  <span>{campaign.sample_size} samples</span>
                  <span>{formatTime(campaign.created_at)}</span>
                </div>
                <div className="text-muted-foreground mt-1 truncate text-xs">
                  {campaign.scope.tenant_id ?? "global"} /{" "}
                  {campaign.scope.environment ?? "all"}
                </div>
              </button>
            ))}
          </div>
          {hasMoreCampaigns ? (
            <div className="text-muted-foreground px-2 py-3 text-xs">
              仅显示最近 50 个批次
            </div>
          ) : null}
        </div>
      </aside>

      <main className="flex min-h-0 flex-col">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate font-mono text-sm font-semibold">
                {activeCampaign?.sample_id}
              </h2>
              <Badge variant="outline">sha256_rank_v1</Badge>
              <Badge variant="secondary">shadow only</Badge>
            </div>
            <div className="text-muted-foreground mt-1 text-xs">
              {activeCampaign
                ? `${formatTime(activeCampaign.scope.window_start)} - ${formatTime(activeCampaign.scope.window_end)}`
                : "-"}
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={refresh}
            disabled={campaignsFetching || inboxFetching}
          >
            <RefreshCwIcon
              className={cn(
                "size-4",
                (campaignsFetching || inboxFetching) && "animate-spin",
              )}
            />
            刷新
          </Button>
        </div>

        {inbox ? (
          <div className="grid grid-cols-2 border-b sm:grid-cols-4">
            <div className="border-r p-4">
              <div className="text-muted-foreground text-xs">完成度</div>
              <div className="mt-1 text-lg font-semibold">
                {formatPercent(inbox.completion_rate)}
              </div>
            </div>
            <div className="border-r p-4">
              <div className="text-muted-foreground text-xs">已完成</div>
              <div className="mt-1 text-lg font-semibold">
                {inbox.completed_count}
              </div>
            </div>
            <div className="border-r p-4">
              <div className="text-muted-foreground text-xs">待完成</div>
              <div className="mt-1 text-lg font-semibold">
                {inbox.remaining_count}
              </div>
            </div>
            <div className="p-4">
              <div className="text-muted-foreground text-xs">Reviewer 冲突</div>
              <div className="mt-1 text-lg font-semibold">
                {inbox.reviewer_conflict_count}
              </div>
            </div>
          </div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {inboxLoading ? (
            <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
              加载复核项...
            </div>
          ) : inboxError ? (
            <div className="text-destructive flex h-40 items-center justify-center p-6 text-sm">
              {inboxError instanceof Error
                ? inboxError.message
                : "抽样复核项加载失败"}
            </div>
          ) : inbox?.items.length ? (
            inbox.items.map((item) => (
              <SampleReviewItem
                key={item.proposal_id}
                item={item}
                onOpenReview={onOpenReview}
              />
            ))
          ) : (
            <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
              当前页没有复核项
            </div>
          )}
        </div>

        {inbox ? (
          <div className="flex items-center justify-between border-t px-4 py-3">
            <div className="text-muted-foreground text-xs">
              {inbox.items.length > 0 ? inbox.offset + 1 : 0}-
              {Math.min(inbox.offset + inbox.items.length, inbox.total_count)} /{" "}
              {inbox.total_count}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="icon-sm"
                title="上一页"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeftIcon className="size-4" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                title="下一页"
                disabled={!inbox.has_more}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                <ChevronRightIcon className="size-4" />
              </Button>
            </div>
          </div>
        ) : null}
      </main>
    </div>
  );
}
