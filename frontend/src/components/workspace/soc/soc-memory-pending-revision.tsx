"use client";

import { FilePenLineIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useSocMemoryCandidates } from "@/core/soc/hooks";

export function SocMemoryPendingRevision({ memoryId }: { memoryId: string }) {
  const { candidates, isLoading, error, refetch, isFetching } =
    useSocMemoryCandidates({ revisionOfMemoryId: memoryId, limit: 2 });
  const candidate = !error && candidates.length === 1 ? candidates[0] : null;
  return (
    <Alert className="border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
      <FilePenLineIcon />
      <AlertTitle>已有待审核的修订</AlertTitle>
      <AlertDescription className="min-w-0 space-y-3">
        <p>旧经验已暂停用于新告警。继续完善已有修订即可，无需重复创建。</p>
        {isLoading ? (
          <p className="flex items-center gap-2">
            <LoaderCircleIcon className="size-4 animate-spin" />
            正在查找修订
          </p>
        ) : candidate ? (
          <>
            <p className="break-words">
              修订原因：{candidate.revision_lineage?.reason}
            </p>
            <Button asChild>
              <Link
                href={`/workspace/soc/review/memory-candidates/${encodeURIComponent(candidate.candidate_id)}`}
              >
                <FilePenLineIcon className="size-4" />
                继续审核修订
              </Link>
            </Button>
          </>
        ) : (
          <div className="space-y-2">
            <p>
              {error
                ? "修订记录暂时加载失败，请重试。"
                : "未能定位唯一待审修订，请刷新状态或到经验治理查看。"}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={isFetching}
                onClick={() => void refetch()}
              >
                <RefreshCwIcon className="size-4" />
                重新查找
              </Button>
              <Button variant="outline" asChild>
                <Link href="/workspace/soc/review/memory-candidates">
                  查看经验治理
                </Link>
              </Button>
            </div>
          </div>
        )}
      </AlertDescription>
    </Alert>
  );
}
