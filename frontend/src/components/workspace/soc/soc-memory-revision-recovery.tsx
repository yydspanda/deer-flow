"use client";

import {
  ArrowUpRightIcon,
  LoaderCircleIcon,
  RotateCcwIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SocMemoryDecisionCapability } from "@/components/workspace/soc/soc-memory-decision-capability";
import {
  useReviewSocMemoryCandidate,
  useSocMemoryRecord,
} from "@/core/soc/hooks";
import type { SocMemoryCandidate } from "@/core/soc/types";

const DAY_MS = 86_400_000;

export function SocMemoryRevisionRecovery({
  candidate,
}: {
  candidate: SocMemoryCandidate;
}) {
  const lineage = candidate.revision_lineage;
  const { record, error, isLoading, refetch } = useSocMemoryRecord(
    lineage?.predecessor_memory_id,
  );
  const review = useReviewSocMemoryCandidate();
  const router = useRouter();
  const [window, setWindow] = useState<{
    version: number;
    validUntil: string;
    reviewDays: number;
  } | null>(null);
  if (!lineage) return null;
  const recordHref = `/workspace/soc/memory/records/${encodeURIComponent(lineage.predecessor_memory_id)}`;
  const rejected = candidate.status === "rejected";
  const eligibleStatus = [
    "pending_review",
    "confirmed_candidate",
    "rejected",
  ].includes(candidate.status);
  const currentRevision =
    record &&
    (rejected
      ? record.metadata.revision_resolution_candidate_id ===
          candidate.candidate_id &&
        record.version === lineage.suspended_record_version + 1
      : record.metadata.revision_pending === true &&
        record.version === lineage.suspended_record_version);
  const canRestore =
    !error &&
    record?.status === "confirmed" &&
    !record.retrieval_enabled &&
    eligibleStatus &&
    currentRevision;
  const actionLabel = rejected ? "恢复旧经验" : "取消修订并恢复旧经验";

  function openConfirmation() {
    if (!record) return;
    const now = Date.now();
    const validUntil = Math.min(
      now + 60 * DAY_MS,
      record.validity.valid_until
        ? Date.parse(record.validity.valid_until)
        : Infinity,
    );
    const reviewDays = Math.min(30, Math.floor((validUntil - now) / DAY_MS));
    if (!Number.isFinite(validUntil) || reviewDays < 1) {
      toast.error(
        "旧经验的有效期不足一天，请先查看旧经验并确认是否需要重新审核。",
      );
      return;
    }
    setWindow({
      version: record.version,
      validUntil: new Date(validUntil).toISOString(),
      reviewDays,
    });
  }

  async function restore() {
    if (!window || review.isPending) return;
    try {
      const result = await review.mutateAsync({
        candidateId: candidate.candidate_id,
        request: {
          decision: "reject",
          reason:
            "运营明确放弃本次修订，恢复旧经验原有内容、适用范围和使用方式。",
          restore_predecessor: true,
          expected_predecessor_version: window.version,
          activation_valid_until: window.validUntil,
          activation_review_after_days: window.reviewDays,
        },
      });
      if (!result.restored_predecessor_record?.retrieval_enabled)
        throw new Error("未收到旧经验已恢复的确认，请刷新状态。");
      setWindow(null);
      toast.success("旧经验已恢复使用，内容和使用方式不变。");
      router.push(recordHref);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "恢复失败，请刷新状态后重试。",
      );
      setWindow(null);
      void refetch();
    }
  }

  return (
    <div className="mt-3 space-y-3" data-memory-revision-recovery>
      <p className="break-words">
        {isLoading
          ? "正在读取旧经验状态。"
          : error
            ? "暂时无法读取旧经验状态，请进入详情刷新。"
            : record?.retrieval_enabled
              ? "旧经验已开放给新告警，本次修订未改变原内容。"
              : record?.superseded_by_memory_id
                ? "旧经验已被新版替代，可查看历史内容。"
                : "旧经验目前暂停使用。放弃修订不会自动恢复，需要你明确确认。"}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant="outline" size="sm">
          <Link href={recordHref}>
            <ArrowUpRightIcon className="size-4" />
            查看旧经验
          </Link>
        </Button>
        {canRestore && (
          <Button
            size="sm"
            onClick={openConfirmation}
            disabled={review.isPending}
          >
            <RotateCcwIcon className="size-4" />
            {actionLabel}
          </Button>
        )}
      </div>
      <Dialog
        open={window !== null}
        onOpenChange={(open) => {
          if (!open && !review.isPending) setWindow(null);
        }}
      >
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{actionLabel}</DialogTitle>
            <DialogDescription>
              本次修订将结束，不创建新经验；恢复旧经验的内容、匹配条件和原有使用方式。
            </DialogDescription>
          </DialogHeader>
          {record && (
            <>
              <p className="text-sm font-medium break-words">
                {record.summary}
              </p>
              <SocMemoryDecisionCapability record={record} />
            </>
          )}
          {window && (
            <p className="text-muted-foreground text-sm leading-6">
              本次开放至{" "}
              {new Date(window.validUntil).toLocaleDateString("zh-CN")}，
              {window.reviewDays}{" "}
              天后需复查；不延长经验本身的有效期。历史研判结果保持不变。
            </p>
          )}
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              disabled={review.isPending}
              onClick={() => setWindow(null)}
            >
              暂不恢复
            </Button>
            <Button disabled={review.isPending} onClick={() => void restore()}>
              {review.isPending ? (
                <LoaderCircleIcon className="size-4 animate-spin" />
              ) : (
                <RotateCcwIcon className="size-4" />
              )}
              确认恢复使用
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
