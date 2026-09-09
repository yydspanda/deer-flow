import {
  ArrowUpRightIcon,
  CheckCircle2Icon,
  CircleHelpIcon,
  XCircleIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { SocCaseOutcomeView } from "@/core/soc";

// Presentation only: classification is supplied by the SOC service.
export function handlingPresentation(
  value: SocCaseOutcomeView["recommended_handling"],
  failed = false,
) {
  if (failed)
    return {
      label: "运行失败",
      icon: XCircleIcon,
      className:
        "border-red-300 bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-300",
    };
  if (value === "ignore")
    return {
      label: "忽略",
      icon: CheckCircle2Icon,
      className:
        "border-emerald-300 bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300",
    };
  if (value === "transfer")
    return {
      label: "转交",
      icon: ArrowUpRightIcon,
      className:
        "border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300",
    };
  return {
    label: "未形成处理结论",
    icon: CircleHelpIcon,
    className: "border-border bg-muted text-muted-foreground",
  };
}

export function SocHandlingBadge({
  value,
  failed = false,
}: {
  value: SocCaseOutcomeView["recommended_handling"];
  failed?: boolean;
}) {
  const item = handlingPresentation(value, failed);
  const Icon = item.icon;
  return (
    <Badge variant="outline" className={item.className}>
      <Icon className="size-3.5 shrink-0" />
      {item.label}
    </Badge>
  );
}
