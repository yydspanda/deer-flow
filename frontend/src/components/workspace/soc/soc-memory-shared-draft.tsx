"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DownloadIcon, SaveIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  getSocMemoryWorkingDraft,
  saveSocMemoryWorkingDraft,
} from "@/core/soc/api";
import type { MemoryCandidateReviewDraft } from "@/core/soc/memory-review-draft";
import {
  workingDraftContent,
  workingDraftPatch,
} from "@/core/soc/memory-working-draft";

export function SocMemorySharedDraft({
  candidateId,
  current,
  hasLocalDraft,
  onChange,
  busy,
}: {
  candidateId: string;
  current: MemoryCandidateReviewDraft;
  hasLocalDraft: boolean;
  onChange: (patch: Partial<MemoryCandidateReviewDraft>) => void;
  busy: boolean;
}) {
  const client = useQueryClient();
  const key = ["soc-memory-working-draft", candidateId];
  const query = useQuery({
    queryKey: key,
    queryFn: () => getSocMemoryWorkingDraft(candidateId),
    retry: false,
  });
  const view = query.data;
  const [confirmation, setConfirmation] = useState<"read" | "rebase" | null>(
    null,
  );
  const sourceChanged =
    !!view &&
    !!current.sharedCandidateRevision &&
    current.sharedCandidateRevision !== view.candidate_revision;
  const versionChanged =
    !!view &&
    current.sharedVersion !== null &&
    current.sharedVersion !== (view.draft?.version ?? 0);
  const save = useMutation({
    mutationFn: () =>
      saveSocMemoryWorkingDraft(candidateId, {
        expected_version: current.sharedVersion ?? 0,
        candidate_revision:
          current.sharedCandidateRevision ?? view!.candidate_revision,
        content: workingDraftContent(current),
      }),
    onSuccess: (result) => {
      client.setQueryData(key, { ...view, draft: result, stale: false });
      // A save acknowledgement must never replace text typed while it was in flight.
      onChange({
        sharedVersion: result.version,
        sharedCandidateRevision: result.candidate_revision,
      });
    },
    onError: () => {
      void query.refetch();
    },
  });

  useEffect(() => {
    if (
      !view?.candidate_revision ||
      !view.editable ||
      current.sharedVersion !== null
    )
      return;
    if (!view.draft) {
      onChange({
        sharedVersion: 0,
        sharedCandidateRevision: view.candidate_revision,
      });
    } else if (!hasLocalDraft && !view.stale) {
      onChange(workingDraftPatch(view.draft));
    }
  }, [view, current.sharedVersion, hasLocalDraft, onChange]);

  const loading = query.isFetching || busy || save.isPending;
  const error = save.error ?? query.error;
  return (
    <div className="my-4 border-y py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className="text-muted-foreground text-xs"
          title={
            view?.draft
              ? `草稿版本 ${view.draft.version}，不代表经验已审核`
              : undefined
          }
        >
          共享草稿 ·{" "}
          {view?.draft
            ? `已保存，${view.draft.updated_by}`
            : query.isLoading
              ? "读取中"
              : "尚未保存"}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => save.mutate()}
          disabled={
            loading ||
            !view?.editable ||
            sourceChanged ||
            versionChanged ||
            (current.sharedVersion === null && !!view.draft)
          }
        >
          <SaveIcon className="size-4" />
          {save.isPending ? "保存中" : "保存草稿"}
        </Button>
        {view?.draft && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={loading}
            onClick={async () => {
              const latest = await query.refetch();
              if (latest.data && !latest.error) setConfirmation("read");
            }}
          >
            <DownloadIcon className="size-4" />
            读取共享草稿
          </Button>
        )}
      </div>
      {versionChanged && (
        <p className="mt-2 text-xs text-amber-800">
          共享草稿已更新。本页修改未被覆盖，请先读取并核对最新内容。
        </p>
      )}
      {sourceChanged && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-amber-800">
          <span>候选来源已更新，旧草稿仍保留。</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={loading || versionChanged}
            onClick={() => setConfirmation("rebase")}
          >
            已核对新来源，保留本页草稿
          </Button>
        </div>
      )}
      {error && (
        <p className="mt-2 text-xs text-red-700" role="alert">
          {error.message}
        </p>
      )}
      <Dialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirmation === "read"
                ? "读取共享草稿？"
                : "确认已核对新的候选来源？"}
            </DialogTitle>
            <DialogDescription>
              {confirmation === "read"
                ? "本页尚未保存的编辑将被共享草稿替换，已审核经验不受影响。"
                : "保留本页文字，以当前候选来源继续编辑。请检查判断依据与适用条件是否仍然成立。"}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmation(null)}>
              取消
            </Button>
            <Button
              disabled={!view || loading}
              onClick={() => {
                if (confirmation === "read" && view?.draft)
                  onChange(workingDraftPatch(view.draft));
                else if (confirmation === "rebase" && view)
                  onChange({
                    sharedCandidateRevision: view.candidate_revision,
                  });
                setConfirmation(null);
                save.reset();
              }}
            >
              确认
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
