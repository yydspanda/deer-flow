import {
  BookOpenCheckIcon,
  ExternalLinkIcon,
  FilePenLineIcon,
} from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { SocCorpusWorkbenchMemoryContext } from "@/core/soc";

export function SocRunMemoryContext({
  memories,
  runId,
}: {
  memories: SocCorpusWorkbenchMemoryContext[];
  runId?: string | null;
}) {
  if (!memories.length) return null;
  return (
    <section
      className="border-b px-5 py-4 md:px-7"
      aria-label="本次研判读取的经验"
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <BookOpenCheckIcon className="size-4 text-emerald-700" />
        本次研判读取的经验
        <Badge variant="outline">{memories.length} 条</Badge>
      </h3>
      <div className="mt-3 divide-y border-y">
        {memories.map((memory) => (
          <div key={memory.context_ref} className="py-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 basis-full space-y-2 sm:flex-1 sm:basis-auto">
                <p className="text-sm font-medium break-words">
                  {memory.label}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Badge
                    variant="outline"
                    className={
                      memory.decision_cited
                        ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                        : undefined
                    }
                  >
                    {memory.decision_cited
                      ? "已被模型引用"
                      : "已提供给模型参考"}
                  </Badge>
                  <Badge variant="outline">
                    {memory.applicability_status === "applicable"
                      ? "适用条件全部命中"
                      : "部分相似，需结合当前行为"}
                  </Badge>
                  {memory.reviewed_verdict ? (
                    <Badge variant="secondary">
                      历史判断：
                      {memory.reviewed_verdict === "false_positive"
                        ? "误报 / 无风险"
                        : memory.reviewed_verdict === "true_positive"
                          ? "真实攻击"
                          : memory.reviewed_verdict === "suspicious"
                            ? "可疑"
                            : "待进一步判断"}
                    </Badge>
                  ) : null}
                </div>
              </div>
              {memory.memory_id ? (
                <div className="flex shrink-0 flex-wrap gap-2">
                  <Button size="sm" variant="outline" asChild>
                    <Link
                      href={`/workspace/soc/memory/records/${encodeURIComponent(memory.memory_id)}`}
                    >
                      <ExternalLinkIcon className="size-4" />
                      查看经验
                    </Link>
                  </Button>
                  {runId ? (
                    <Button size="sm" variant="outline" asChild>
                      <Link
                        href={`/workspace/soc/memory/records/${encodeURIComponent(memory.memory_id)}/revise?run_id=${encodeURIComponent(runId)}`}
                      >
                        <FilePenLineIcon className="size-4" />
                        不适用？发起修订
                      </Link>
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </div>
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer font-medium">
                本次读取版本与经验原文
              </summary>
              <p className="text-muted-foreground mt-2 break-all">
                {memory.source_id} · {memory.context_ref}
              </p>
              <p className="mt-2 leading-6 break-words">{memory.summary}</p>
            </details>
          </div>
        ))}
      </div>
    </section>
  );
}
