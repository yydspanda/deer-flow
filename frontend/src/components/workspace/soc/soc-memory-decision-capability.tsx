import { BookOpenCheckIcon, ShieldCheckIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { memoryDecisionCapabilityCopy } from "@/components/workspace/soc/soc-memory-copy";
import type { SocMemoryRecord } from "@/core/soc";

const VERDICT_LABELS: Record<string, string> = {
  false_positive: "误报",
  true_positive: "真实攻击",
  suspicious: "可疑",
  unknown: "未知",
};

export function SocMemoryDecisionCapability({
  record,
}: {
  record: SocMemoryRecord;
}) {
  const directive = record.decision_directive;
  const capability = memoryDecisionCapabilityCopy(Boolean(directive));
  if (!directive) {
    return (
      <Alert
        data-memory-use-mode="reference_only"
        className="rounded-md border-sky-300 bg-sky-50 text-sky-950 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-100"
      >
        <BookOpenCheckIcon />
        <AlertTitle className="line-clamp-none flex flex-wrap items-center gap-2">
          <span>未来告警如何使用</span>
          <Badge
            data-memory-use-primary="true"
            className="bg-sky-700 px-2.5 text-white hover:bg-sky-700 dark:bg-sky-600"
          >
            {capability.label}
          </Badge>
        </AlertTitle>
        <AlertDescription className="text-sky-900/80 dark:text-sky-100/80">
          <span>{capability.detail}</span>
          <span className="font-medium">
            使用边界：参与模型研判上下文，不直接改判，也不授权外部动作。
          </span>
        </AlertDescription>
      </Alert>
    );
  }

  const effectLabel = directive.effect === "override" ? "改判" : "强化结论";
  const targetLabel =
    VERDICT_LABELS[directive.target_verdict] ?? directive.target_verdict;
  const capabilityDescription = `新告警完整满足审核过的必需条件，并达到 ${directive.minimum_match_score} 分匹配门槛时，可将结论${effectLabel}为“${targetLabel}”。只有部分条件相似时，仅供模型参考，不直接改判。`;
  return (
    <Alert
      data-memory-use-mode="exact_match_decision"
      className="rounded-md border-emerald-300 bg-emerald-50 text-emerald-950 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-100"
    >
      <ShieldCheckIcon />
      <AlertTitle className="line-clamp-none flex flex-wrap items-center gap-2">
        <span>未来告警如何使用</span>
        <Badge
          data-memory-use-primary="true"
          className="bg-emerald-700 px-2.5 text-white hover:bg-emerald-700"
        >
          精确匹配可复用结论
        </Badge>
      </AlertTitle>
      <AlertDescription className="text-emerald-900/80 dark:text-emerald-100/80">
        {capabilityDescription}
      </AlertDescription>
    </Alert>
  );
}
