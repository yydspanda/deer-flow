import { BrainCircuitIcon, ExternalLinkIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { memoryLearningHref } from "@/core/soc/memory-learning";
import type { SocMemoryLearningView } from "@/core/soc/types";

export function SocMemoryLearningStatus({
  view,
}: {
  view: SocMemoryLearningView;
}) {
  const href = memoryLearningHref(view);
  if (!href) return null;
  return (
    <section
      className="flex flex-wrap items-center justify-between gap-4 border-y border-sky-200 bg-sky-50 px-5 py-4 text-sky-950"
      role="status"
    >
      <div className="flex min-w-0 items-start gap-3">
        <BrainCircuitIcon className="mt-0.5 size-5 shrink-0" />
        <div className="min-w-0">
          <p className="font-semibold">{view.label}</p>
          <p className="mt-1 text-sm">{view.detail}</p>
        </div>
      </div>
      <Button size="sm" asChild>
        <Link href={href}>
          {view.action_label}
          <ExternalLinkIcon className="size-3.5" />
        </Link>
      </Button>
    </section>
  );
}
