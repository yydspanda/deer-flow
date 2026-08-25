"use client";

import {
  CheckCircle2Icon,
  CircleAlertIcon,
  ListChecksIcon,
  PresentationIcon,
  TargetIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type SocCorpusWorkbenchAlert,
  type SocCorpusWorkbenchGroup,
  type SocLeadershipDemoGuide,
  type SocLeadershipDemoTarget,
} from "@/core/soc";
import { cn } from "@/lib/utils";

function targetRuntimeStatus(
  target: SocLeadershipDemoTarget,
  alertsById: Map<string, SocCorpusWorkbenchAlert>,
) {
  const alerts = target.rehearsal_alert_ids
    .map((alertId) => alertsById.get(alertId))
    .filter((alert): alert is SocCorpusWorkbenchAlert => alert !== undefined);
  return {
    completed: alerts.filter((alert) => alert.workflow_state === "completed")
      .length,
    memoryHits: alerts.filter((alert) => alert.memory_contexts.length > 0)
      .length,
    candidates: alerts.filter((alert) =>
      Boolean(alert.candidate_id ?? alert.manual_candidate_id),
    ).length,
  };
}

export function SocLeadershipDemoGuidePanel({
  guide,
  groups,
  alerts,
  activeGroupId,
  onSelectTarget,
}: {
  guide: SocLeadershipDemoGuide;
  groups: SocCorpusWorkbenchGroup[];
  alerts: SocCorpusWorkbenchAlert[];
  activeGroupId: string;
  onSelectTarget: (target: SocLeadershipDemoTarget) => void;
}) {
  const groupsById = new Map(groups.map((group) => [group.group_id, group]));
  const alertsById = new Map(alerts.map((alert) => [alert.alert_id, alert]));

  return (
    <section aria-labelledby="capability-guide-title" className="border-b">
      <div className="flex flex-wrap items-start justify-between gap-4 bg-zinc-950 px-5 py-5 text-white md:px-7">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2 text-sm font-medium text-emerald-300">
            <PresentationIcon className="size-4" />
            Scenario Guide / 场景导览
          </div>
          <h2
            id="capability-guide-title"
            className="mt-2 text-lg font-semibold"
          >
            {guide.title}
          </h2>
          <p className="mt-1 text-sm leading-6 text-zinc-300">
            {guide.purpose}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge className="border-emerald-500/60 bg-emerald-950 text-emerald-200">
            {guide.primary_chapter_count} 组主线
          </Badge>
          <Badge className="border-zinc-600 bg-zinc-900 text-zinc-200">
            {guide.backup_chapter_count} 组备选
          </Badge>
          <Badge
            className={cn(
              guide.ready
                ? "border-emerald-500/60 bg-emerald-950 text-emerald-200"
                : "border-amber-500/60 bg-amber-950 text-amber-200",
            )}
          >
            {guide.ready ? "语料校验通过" : "存在语料漂移"}
          </Badge>
        </div>
      </div>

      <div className="divide-y">
        {guide.chapters.map((chapter) => (
          <div
            key={chapter.chapter_id}
            className={cn(
              "grid gap-4 px-5 py-4 md:grid-cols-[2.5rem_minmax(0,1fr)_minmax(17rem,0.75fr)] md:px-7",
              chapter.tier === "backup" && "bg-zinc-50/70",
            )}
          >
            <div className="flex size-9 items-center justify-center border bg-white text-sm font-semibold tabular-nums">
              {String(chapter.sequence).padStart(2, "0")}
            </div>

            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-semibold">{chapter.title}</h3>
                <Badge variant="outline">
                  {chapter.tier === "primary" ? "主线" : "备选"}
                </Badge>
              </div>
              <p className="text-muted-foreground mt-1 text-sm leading-6">
                {chapter.objective}
              </p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {chapter.capabilities.map((capability) => (
                  <Badge key={capability} variant="secondary">
                    {capability}
                  </Badge>
                ))}
              </div>
              <details className="mt-3 text-sm">
                <summary className="text-muted-foreground hover:text-foreground cursor-pointer font-medium">
                  演示步骤与讲解提示
                </summary>
                <div className="mt-3 grid gap-4 border-l-2 pl-4 lg:grid-cols-2">
                  <div>
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase">
                      <ListChecksIcon className="size-3.5" />
                      操作顺序
                    </div>
                    <ol className="text-muted-foreground mt-2 space-y-1.5 text-xs leading-5">
                      {chapter.operator_steps.map((step, index) => (
                        <li key={step}>
                          {index + 1}. {step}
                        </li>
                      ))}
                    </ol>
                  </div>
                  <div>
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase">
                      <CheckCircle2Icon className="size-3.5" />
                      现场验收点
                    </div>
                    <ul className="text-muted-foreground mt-2 space-y-1.5 text-xs leading-5">
                      {chapter.success_cues.map((cue) => (
                        <li key={cue}>· {cue}</li>
                      ))}
                    </ul>
                  </div>
                </div>
                <p className="mt-3 border-l-2 border-amber-300 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                  讲解口径：{chapter.presenter_note}
                </p>
              </details>
            </div>

            <div className="space-y-2">
              {chapter.targets.map((target) => {
                const group = groupsById.get(target.actual_group_id ?? "");
                const status = targetRuntimeStatus(target, alertsById);
                const active =
                  target.actual_group_id !== null &&
                  activeGroupId === target.actual_group_id;
                const disabled = target.availability !== "ready";
                return (
                  <div
                    key={target.target_id}
                    className={cn(
                      "border px-3 py-3",
                      active && "border-emerald-500 bg-emerald-50",
                      disabled && "border-amber-300 bg-amber-50",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{target.label}</p>
                        <p className="text-muted-foreground mt-1 font-mono text-[11px]">
                          {target.expected_group_id} ·{" "}
                          {target.source_type.toUpperCase()}
                        </p>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant={active ? "default" : "outline"}
                        disabled={disabled}
                        onClick={() => onSelectTarget(target)}
                      >
                        {disabled ? (
                          <CircleAlertIcon className="size-3.5" />
                        ) : (
                          <TargetIcon className="size-3.5" />
                        )}
                        {active ? "已定位" : "定位案例"}
                      </Button>
                    </div>
                    <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                      <span>{group?.alert_count ?? 0} 条同类</span>
                      <span>
                        演练 {status.completed}/
                        {target.rehearsal_alert_ids.length}
                      </span>
                      <span>Candidate {status.candidates}</span>
                      <span>Memory 命中 {status.memoryHits}</span>
                    </div>
                    {disabled ? (
                      <p className="mt-2 text-xs text-amber-900">
                        预期语料已缺失或重新分组，请先更新演示清单后再汇报。
                      </p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
