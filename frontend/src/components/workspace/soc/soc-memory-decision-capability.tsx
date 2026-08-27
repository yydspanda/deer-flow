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
      <Alert className="rounded-md">
        <BookOpenCheckIcon />
        <AlertTitle className="flex flex-wrap items-center gap-2">
          <span>未来告警如何使用</span>
          <Badge variant="secondary">{capability.label}</Badge>
        </AlertTitle>
        <AlertDescription>{capability.detail}</AlertDescription>
      </Alert>
    );
  }

  const effectLabel = directive.effect === "override" ? "改判" : "强化结论";
  const targetLabel =
    VERDICT_LABELS[directive.target_verdict] ?? directive.target_verdict;
  const capabilityDescription = `新告警完整满足审核过的必需条件，并达到 ${directive.minimum_match_score} 分匹配门槛时，可将结论${effectLabel}为“${targetLabel}”。只有部分条件相似时，仅供模型参考，不直接改判。`;
  return (
    <Alert className="rounded-md border-emerald-300 bg-emerald-50 text-emerald-950 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-100">
      <ShieldCheckIcon />
      <AlertTitle className="flex flex-wrap items-center gap-2">
        <span>未来告警如何使用</span>
        <Badge className="bg-emerald-700 text-white hover:bg-emerald-700">
          精确匹配可复用结论
        </Badge>
      </AlertTitle>
      <AlertDescription>{capabilityDescription}</AlertDescription>
    </Alert>
  );
}
