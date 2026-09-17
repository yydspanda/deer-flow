"use client";

import { GitBranchIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  useRefineSocMemoryScope,
  useSocMemoryGovernancePreview,
} from "@/core/soc/hooks";
import type { SocMemoryCandidate } from "@/core/soc/types";

import { SocMemoryScope } from "./soc-memory-scope";

export function SocMemoryScopeRefinement({
  candidate,
}: {
  candidate: SocMemoryCandidate;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-4 border-t pt-3">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <GitBranchIcon className="size-4" />
        {open ? "取消细分" : "为部分告警建立细分经验"}
      </Button>
      {open && <RefinementEditor candidate={candidate} />}
    </div>
  );
}

function RefinementEditor({ candidate }: { candidate: SocMemoryCandidate }) {
  const router = useRouter();
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [behavior, setBehavior] = useState(
    candidate.applicability?.selected_behavior_components ?? null,
  );
  const [notice, setNotice] = useState("");
  const mutation = useRefineSocMemoryScope();
  const { data, isFetching, error } = useSocMemoryGovernancePreview(
    candidate.candidate_id,
    null,
    selected,
    behavior,
  );
  const count = data?.sample_coverage?.applicable ?? 0;
  return (
    <section className="mt-3 space-y-3" aria-label="细分经验范围">
      <h3 className="text-sm font-semibold">细分经验范围</h3>
      <p className="text-muted-foreground text-sm">
        保留原经验及其余样本，为所选范围单独审核业务结论。尚未确认的新经验不会参与复用。
      </p>
      <SocMemoryScope
        candidateId={candidate.candidate_id}
        spec={candidate.applicability}
        view={candidate.scope_view}
        promoted={selected}
        onPromote={setSelected}
        selectedBehavior={behavior}
        onSelectBehavior={setBehavior}
        disabled={mutation.isPending}
      />
      <p className="text-sm font-medium">
        覆盖 {count} / {data?.sample_coverage?.total ?? 0} 条来源样本
      </p>
      {(error ?? mutation.error) && (
        <p role="alert" className="text-destructive text-sm">
          {(error ?? mutation.error)?.message}
        </p>
      )}
      {notice && (
        <p role="status" className="text-sm">
          {notice}
        </p>
      )}
      <Button
        type="button"
        disabled={isFetching || mutation.isPending || !count || !!error}
        onClick={async () => {
          setNotice("");
          try {
            const result = await mutation.mutateAsync({
              candidate_id: candidate.candidate_id,
              expected_updated_at: candidate.updated_at,
              promoted_facet_values: selected,
              selected_behavior_components: behavior,
            });
            if (result.candidate_id === candidate.candidate_id)
              setNotice("条件未收窄，继续使用当前审核记录即可。");
            else
              router.push(
                `/workspace/soc/review/memory-candidates/${encodeURIComponent(result.candidate_id)}`,
              );
          } catch {
            /* The mutation error stays next to the unchanged selections. */
          }
        }}
      >
        <GitBranchIcon className="size-4" />
        {mutation.isPending ? "正在创建..." : "创建细分候选并审核"}
      </Button>
    </section>
  );
}
