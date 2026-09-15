"use client";

import { ArrowRightLeftIcon, ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { Badge } from "@/components/ui/badge";
import { useSocMemoryGovernancePreview } from "@/core/soc/hooks";
import type { SocVerdict } from "@/core/soc/types";

const verdictLabels: Record<SocVerdict, string> = {
  false_positive: "误报 / 无风险",
  true_positive: "真实攻击",
  suspicious: "可疑",
  unknown: "尚未确定",
  needs_review: "待确认",
};
const scopeLabels = {
  same: "适用条件相同",
  overlap: "适用条件可能重叠",
  disjoint: "精确适用条件不同",
  unknown: "适用条件尚不能比较",
};

export function SocMemoryGovernancePanel({
  candidateId,
  verdict,
  promotedFacets,
  selectedBehavior,
  replacement,
  onReplace,
  facetLabel,
}: {
  candidateId: string;
  verdict: SocVerdict | null;
  promotedFacets: Record<string, string[]>;
  selectedBehavior?: string[] | null;
  replacement: { memoryId: string; version: number } | null;
  onReplace: (value: { memoryId: string; version: number } | null) => void;
  facetLabel: (key: string) => string;
}) {
  const { data, isLoading, error, refetch } = useSocMemoryGovernancePreview(
    candidateId,
    verdict,
    promotedFacets,
    selectedBehavior,
  );
  useEffect(() => {
    if (!data || !replacement) return;
    const selected = data.related_memories.find(
      (item) => item.memory_id === replacement.memoryId,
    );
    if (
      selected?.version !== replacement.version ||
      !["same", "overlap"].includes(selected.scope_relation)
    )
      onReplace(null);
  }, [data, replacement, onReplace]);
  if (isLoading)
    return (
      <p role="status" className="text-muted-foreground mt-4 text-sm">
        正在核对已有经验...
      </p>
    );
  if (error)
    return (
      <div role="alert" className="text-destructive mt-4 text-sm">
        已有经验暂未加载。
        <button
          type="button"
          className="ml-2 underline"
          onClick={() => void refetch()}
        >
          重新加载
        </button>
      </div>
    );
  if (!data || data.related_count === 0) return null;

  return (
    <section
      aria-label="与已有经验对照"
      className="my-5 border-y border-teal-200 bg-teal-50/40 px-4 py-4 dark:border-teal-900 dark:bg-teal-950/20"
    >
      <h4 className="flex items-center gap-2 text-sm font-semibold">
        <ArrowRightLeftIcon className="size-4 shrink-0" />
        与已有经验对照
      </h4>
      <p className="mt-2 text-sm leading-6">{data.explanation}</p>
      <div className="mt-3 divide-y">
        {data.related_memories.map((memory) => (
          <div key={memory.memory_id} className="py-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant="secondary">
                {memory.reviewed_verdict
                  ? verdictLabels[memory.reviewed_verdict]
                  : "未记录审核结论"}
              </Badge>
              {memory.conclusion_relation === "differs" && (
                <span className="font-medium text-amber-800 dark:text-amber-200">
                  与本次审核结论不同
                </span>
              )}
              <span>{scopeLabels[memory.scope_relation]}</span>
              <span className="text-muted-foreground">
                {memory.retrieval_enabled
                  ? memory.directive_enabled
                    ? "精确匹配可复用结论"
                    : "仅供研判参考"
                  : "暂停使用"}
              </span>
              {memory.retrieved_in_source_run && <span>本次告警曾参考</span>}
            </div>
            <p className="mt-2 text-sm leading-6 break-words">
              {memory.conclusion}
            </p>
            {memory.differences.length > 0 && (
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer font-medium">
                  条件差异（{memory.differences.length}）
                </summary>
                <dl className="mt-2 space-y-2">
                  {memory.differences.map((difference) => (
                    <div
                      key={difference.facet}
                      className="grid min-w-0 gap-1 sm:grid-cols-[10rem_minmax(0,1fr)]"
                    >
                      <dt className="font-medium break-words">
                        {facetLabel(difference.facet)}
                      </dt>
                      <dd className="min-w-0 break-all">
                        本次：
                        {difference.candidate_values.join(" / ") || "未限制"}
                        <br />
                        已有：{difference.memory_values.join(" / ") || "未限制"}
                      </dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
              {["same", "overlap"].includes(memory.scope_relation) && (
                <label className="flex cursor-pointer items-center gap-2 font-medium">
                  <input
                    type="radio"
                    name={`memory-resolution-${candidateId}`}
                    checked={replacement?.memoryId === memory.memory_id}
                    onChange={() =>
                      onReplace({
                        memoryId: memory.memory_id,
                        version: memory.version,
                      })
                    }
                  />
                  以本次审核修订这条经验
                </label>
              )}
              <Link
                className="inline-flex items-center gap-1 underline underline-offset-4"
                href={`/workspace/soc/memory/records/${encodeURIComponent(memory.memory_id)}`}
                target="_blank"
                rel="noreferrer"
              >
                查看原经验
                <ExternalLinkIcon className="size-3" />
              </Link>
            </div>
          </div>
        ))}
      </div>
      <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm">
        <input
          type="radio"
          name={`memory-resolution-${candidateId}`}
          checked={replacement === null}
          onChange={() => onReplace(null)}
        />
        保留为独立经验
      </label>
      {replacement && (
        <p className="mt-2 text-sm font-medium text-teal-800 dark:text-teal-200">
          确认后，新经验接替旧经验；历史告警及旧结论保留。现在尚未修改原经验。
        </p>
      )}
    </section>
  );
}
