"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { BookCheckIcon, InboxIcon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
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
import { Tooltip } from "@/components/workspace/tooltip";
import { useAcceptSocLeadAgentConclusion } from "@/core/soc";
import { latestSocAssistantConclusion } from "@/core/soc/agent-review";

export function SocAgentReviewActions({
  reviewQueueId,
  threadId,
  messages,
  isNewThread,
  isMock,
  isLoading,
}: {
  reviewQueueId: string | null;
  threadId: string;
  messages: Message[];
  isNewThread: boolean;
  isMock: boolean;
  isLoading: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const acceptConclusion = useAcceptSocLeadAgentConclusion();
  const conclusion = useMemo(
    () => latestSocAssistantConclusion(messages),
    [messages],
  );
  const canAccept =
    reviewQueueId !== null &&
    conclusion !== null &&
    !isNewThread &&
    !isMock &&
    !isLoading &&
    !acceptConclusion.isPending;

  async function handleAccept() {
    const acceptanceReason = reason.trim();
    if (!canAccept || !reviewQueueId || !conclusion || !acceptanceReason)
      return;
    try {
      const result = await acceptConclusion.mutateAsync({
        queueId: reviewQueueId,
        threadId,
        request: {
          message_id: conclusion.messageId,
          acceptance_reason: acceptanceReason,
        },
      });
      setOpen(false);
      setReason("");
      toast.success(
        result.memory_candidate
          ? `已生成待审核记忆候选 ${result.memory_candidate.candidate_id}`
          : "Lead Agent 结论已记录",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "采纳结论失败");
    }
  }

  if (!reviewQueueId) return null;
  return (
    <>
      <Tooltip content={`返回复核队列 ${reviewQueueId}`}>
        <Button asChild size="sm" variant="ghost">
          <Link href="/workspace/soc/review/alerts">
            <InboxIcon />
            <span className="hidden max-w-36 truncate sm:inline">
              {reviewQueueId}
            </span>
          </Link>
        </Button>
      </Tooltip>
      <Tooltip content="将最后一条完整回复提交为待审核记忆候选">
        <Button
          size="sm"
          variant="outline"
          disabled={!canAccept}
          onClick={() => setOpen(true)}
        >
          <BookCheckIcon />
          <span className="hidden sm:inline">采纳结论</span>
        </Button>
      </Tooltip>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>采纳 Lead Agent 结论</DialogTitle>
            <DialogDescription>
              确认后，这条结论将进入待审核记忆候选，不会直接影响后续研判。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-muted-foreground line-clamp-4 text-sm">
              {conclusion?.text}
            </p>
            <Textarea
              value={reason}
              maxLength={2000}
              placeholder="填写你核验并采纳该结论的理由"
              rows={4}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              disabled={!canAccept || reason.trim().length === 0}
              onClick={() => void handleAccept()}
            >
              <BookCheckIcon />
              提交候选
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
