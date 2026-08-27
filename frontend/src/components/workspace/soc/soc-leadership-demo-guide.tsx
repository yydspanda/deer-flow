"use client";

import {
  CheckCircle2Icon,
  CircleAlertIcon,
  FileSearchIcon,
  GitCompareArrowsIcon,
  LightbulbIcon,
  ShieldCheckIcon,
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

const MEMORY_MODE_COPY = {
  context_only: {
    label: "只作研判参考",
    description: "规则相同，但实际行为不同。历史经验帮助理解背景，不直接改判。",
    icon: LightbulbIcon,
    className: "border-sky-300 bg-sky-50 text-sky-900",
  },
  exact_match: {
    label: "精确匹配复用",
    description: "规则和强行为特征都一致。审核结论可参与本次最终判断。",
    icon: ShieldCheckIcon,
    className: "border-emerald-300 bg-emerald-50 text-emerald-900",
  },
} as const;

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
  const ruleCodes = new Set(
    guide.chapters.flatMap((chapter) =>
      chapter.targets
        .map(
          (target) => groupsById.get(target.actual_group_id ?? "")?.rule_code,
        )
        .filter((value): value is string => Boolean(value)),
    ),
  );
  const sharedRuleCode = ruleCodes.size === 1 ? [...ruleCodes][0] : null;

  return (
    <section aria-labelledby="rehearsal-guide-title" className="border-b">
      <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-5 md:px-7">
        <div className="max-w-3xl">
          <div className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
            <GitCompareArrowsIcon className="size-4" />
            推荐演练
          </div>
          <h2
            id="rehearsal-guide-title"
            className="mt-1.5 text-lg font-semibold"
          >
            {guide.title}
          </h2>
          <p className="text-muted-foreground mt-1 text-sm leading-6">
            {guide.purpose}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {sharedRuleCode ? (
            <Badge variant="outline" className="font-mono">
              同一规则 {sharedRuleCode}
            </Badge>
          ) : null}
          <Badge
            variant="outline"
            className={cn(
              guide.ready
                ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                : "border-amber-300 bg-amber-50 text-amber-900",
            )}
          >
            {guide.ready ? (
              <CheckCircle2Icon className="size-3.5" />
            ) : (
              <CircleAlertIcon className="size-3.5" />
            )}
            {guide.ready ? "两组告警已就绪" : "演练样本需要更新"}
          </Badge>
        </div>
      </div>

      <div className="grid border-t lg:grid-cols-2">
        {guide.chapters.map((chapter, index) => {
          const mode = MEMORY_MODE_COPY[chapter.expected_memory_use];
          const ModeIcon = mode.icon;
          const target = chapter.targets[0];
          if (!target) return null;
          const group = groupsById.get(target.actual_group_id ?? "");
          const status = targetRuntimeStatus(target, alertsById);
          const active =
            target.actual_group_id !== null &&
            activeGroupId === target.actual_group_id;
          const disabled = target.availability !== "ready";

          return (
            <article
              key={chapter.chapter_id}
              className={cn(
                "min-w-0 px-5 py-5 md:px-7",
                index > 0 && "border-t lg:border-t-0 lg:border-l",
                active && "bg-sky-50/50",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="flex size-7 items-center justify-center border bg-white text-xs font-semibold tabular-nums">
                      {chapter.sequence}
                    </span>
                    <Badge variant="outline" className={mode.className}>
                      <ModeIcon className="size-3.5" />
                      {mode.label}
                    </Badge>
                  </div>
                  <h3 className="mt-3 text-base font-semibold">
                    {chapter.title}
                  </h3>
                  <p className="text-muted-foreground mt-1 text-sm leading-6">
                    {mode.description}
                  </p>
                </div>
              </div>

              <div className="mt-4 grid gap-3 border-y py-3 text-sm sm:grid-cols-2">
                <div className="min-w-0">
                  <p className="text-muted-foreground text-xs">演练行为</p>
                  <p className="mt-1 font-medium">{target.label}</p>
                </div>
                <div className="min-w-0">
                  <p className="text-muted-foreground text-xs">当前进度</p>
                  <p className="mt-1 tabular-nums">
                    已运行 {status.completed}/
                    {target.rehearsal_alert_ids.length}
                    {status.memoryHits > 0
                      ? ` · ${status.memoryHits} 条使用过经验`
                      : " · 尚未使用经验"}
                  </p>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <div className="text-muted-foreground text-xs">
                  <span>{group?.alert_count ?? 0} 条同场景样本</span>
                  <span className="mx-1.5">·</span>
                  <span className="font-mono">
                    Alert {target.primary_alert_id}
                  </span>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant={active ? "secondary" : "default"}
                  disabled={disabled}
                  onClick={() => onSelectTarget(target)}
                >
                  {disabled ? (
                    <CircleAlertIcon className="size-4" />
                  ) : (
                    <FileSearchIcon className="size-4" />
                  )}
                  {active ? "已显示这组" : "查看这组告警"}
                </Button>
              </div>

              <details className="mt-4 text-sm">
                <summary className="text-muted-foreground hover:text-foreground cursor-pointer font-medium">
                  查看操作步骤和预期结果
                </summary>
                <div className="mt-3 grid gap-4 border-l-2 pl-4 sm:grid-cols-2">
                  <div>
                    <p className="text-xs font-semibold">操作步骤</p>
                    <ol className="text-muted-foreground mt-2 space-y-1.5 text-xs leading-5">
                      {chapter.operator_steps.map((step, stepIndex) => (
                        <li key={step}>
                          {stepIndex + 1}. {step}
                        </li>
                      ))}
                    </ol>
                  </div>
                  <div>
                    <p className="text-xs font-semibold">验收结果</p>
                    <ul className="text-muted-foreground mt-2 space-y-1.5 text-xs leading-5">
                      {chapter.success_cues.map((cue) => (
                        <li key={cue}>· {cue}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </details>
            </article>
          );
        })}
      </div>
    </section>
  );
}
