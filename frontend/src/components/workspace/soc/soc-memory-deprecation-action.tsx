"use client";

import { LoaderCircleIcon, XCircleIcon } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { useReviewSocMemoryCandidate, type SocMemoryRecord } from "@/core/soc";

export function SocMemoryDeprecationAction({
  record,
  disabled = false,
  onDeprecated,
}: {
  record: SocMemoryRecord;
  disabled?: boolean;
  onDeprecated?: () => void;
}) {
  const reviewMutation = useReviewSocMemoryCandidate();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const revisionPending = record.metadata.revision_pending === true;
  const unavailable = disabled || revisionPending || reviewMutation.isPending;

  const handleDeprecate = async () => {
    if (unavailable || reason.trim().length < 10) return;
    setError(null);
    try {
      await reviewMutation.mutateAsync({
        candidateId: record.source_candidate_id,
        request: { decision: "deprecate", reason: reason.trim() },
      });
      setOpen(false);
      toast.success("经验已废止，不再用于新告警，历史记录已保留");
      onDeprecated?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "废止失败，请重试");
    }
  };

  if (record.status !== "confirmed") return null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
      <div className="min-w-0 flex-1 basis-64">
        <p className="text-sm font-medium">不再使用这条经验</p>
        <p className="text-muted-foreground mt-1 text-xs leading-5">
          {revisionPending
            ? "已有待审核的修订，请先完成或取消该修订，再决定是否废止旧经验。"
            : "无需修改内容，可直接废止，不创建新版本；历史告警结论和使用记录仍保留。"}
        </p>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="text-destructive border-destructive/40 hover:bg-destructive/10 hover:text-destructive shrink-0"
        disabled={unavailable}
        onClick={() => {
          setReason("");
          setError(null);
          setOpen(true);
        }}
      >
        <XCircleIcon className="size-4" />
        废止这条经验
      </Button>
      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!reviewMutation.isPending) setOpen(nextOpen);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>废止这条经验</DialogTitle>
            <DialogDescription>
              这不是临时暂停，也不会创建修订候选。废止后，新告警不能再参考或直接复用这条经验；历史告警、确认记录和使用记录仍会保留。
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm font-medium break-words">{record.summary}</p>
          <label className="grid gap-2 text-sm">
            <span className="font-medium">废止原因</span>
            <Textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明这条经验为什么已经错误、过时或不应继续使用"
              rows={4}
              disabled={reviewMutation.isPending}
            />
            <span className="text-muted-foreground text-xs">
              至少 10 个字符，将与操作人和操作时间一同留存。
            </span>
          </label>
          {error ? (
            <p role="alert" className="text-destructive text-sm break-words">
              {error}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={reviewMutation.isPending}
              onClick={() => setOpen(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={unavailable || reason.trim().length < 10}
              onClick={() => void handleDeprecate()}
            >
              {reviewMutation.isPending ? (
                <LoaderCircleIcon className="size-4 animate-spin" />
              ) : (
                <XCircleIcon className="size-4" />
              )}
              确认废止
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
