import {
  AlertTriangleIcon,
  BrainCircuitIcon,
  Building2Icon,
  CheckCircle2Icon,
  Clock3Icon,
  FileSearchIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  XCircleIcon,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type {
  SocAnalysisCapability,
  SocCaseClosureStatus,
  SocCaseEvidenceGapImpact,
  SocCaseOutcomeBasisKind,
  SocCaseOutcomeView,
  SocOperationalDisposition,
  SocVerdict,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const VERDICT_LABELS: Record<SocVerdict, string> = {
  true_positive: "真实攻击",
  false_positive: "误报 / 无风险",
  suspicious: "可疑",
  unknown: "暂无法判断",
  needs_review: "需要进一步确认",
};

const DISPOSITION_LABELS: Record<SocOperationalDisposition, string> = {
  closed_true_positive: "确认攻击并结案",
  closed_false_positive: "误报结案",
  closed_benign_true_positive: "授权活动结案",
  suppressed: "抑制后续告警",
  escalated: "转交处置",
  ignored: "忽略",
  duplicate: "合并为重复告警",
  unknown: "处置未明确",
};

const CLOSURE: Record<
  SocCaseClosureStatus,
  {
    label: string;
    description: string;
    icon: LucideIcon;
    className: string;
  }
> = {
  closed: {
    label: "处理完成",
    description: "安全判断和运营处置均已明确。",
    icon: CheckCircle2Icon,
    className:
      "border-emerald-300 bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300",
  },
  closed_with_limitations: {
    label: "处理完成，有补充信息",
    description: "当前结果有效，剩余信息不阻断本次处置。",
    icon: CheckCircle2Icon,
    className:
      "border-sky-300 bg-sky-50 text-sky-800 dark:bg-sky-950/30 dark:text-sky-300",
  },
  handling_pending: {
    label: "研判完成，处置待执行",
    description: "安全判断已经形成，运营动作尚未记录为完成。",
    icon: Clock3Icon,
    className:
      "border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300",
  },
  follow_up_required: {
    label: "关键事实待确认",
    description: "当前存在会影响最终判断或处置的关键问题。",
    icon: AlertTriangleIcon,
    className:
      "border-red-300 bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-300",
  },
  failed: {
    label: "处理失败",
    description: "本次运行没有形成可用结果，需要修复后重试。",
    icon: XCircleIcon,
    className:
      "border-red-300 bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-300",
  },
};

const GAP_IMPACT: Record<
  SocCaseEvidenceGapImpact,
  { label: string; description: string; className: string }
> = {
  none: {
    label: "关键证据已覆盖",
    description: "当前没有需要特别说明的证据缺口。",
    className:
      "border-emerald-300 bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300",
  },
  advisory: {
    label: "补充信息，不影响当前结论",
    description: "这些信息可以完善调查记录，但不会改变本次安全判断。",
    className:
      "border-sky-300 bg-sky-50 text-sky-800 dark:bg-sky-950/30 dark:text-sky-300",
  },
  capability_limited: {
    label: "安全判断可用，部分自动化受限",
    description: "缺口不推翻当前判断，但依赖精确目标的能力不会自动执行。",
    className:
      "border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300",
  },
  decision_blocking: {
    label: "关键问题影响闭环",
    description: "必须先补充或裁决这些事实，不能把当前结果当作已完成。",
    className:
      "border-red-300 bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-300",
  },
};

const BASIS_LABELS: Record<SocCaseOutcomeBasisKind, string> = {
  current_analysis: "当前告警事实与模型研判",
  confirmed_memory: "已审核经验",
  tenant_policy: "企业处置规则",
  external_feedback: "外部系统最终反馈",
};

const BASIS_ICONS: Record<SocCaseOutcomeBasisKind, LucideIcon> = {
  current_analysis: FileSearchIcon,
  confirmed_memory: BrainCircuitIcon,
  tenant_policy: Building2Icon,
  external_feedback: RefreshCwIcon,
};

const CAPABILITY_LABELS: Record<SocAnalysisCapability, string> = {
  scenario_routing: "场景路由",
  network_direction: "网络方向判断",
  source_targeting: "来源目标定位",
  destination_targeting: "目的目标定位",
  attacker_targeting: "攻击者定位",
  victim_targeting: "受害者定位",
  impacted_asset_targeting: "受影响资产定位",
  user_targeting: "账号定位",
  response_action: "自动响应动作",
};

function verdictClass(verdict?: SocVerdict | null) {
  if (verdict === "true_positive") {
    return "border-red-300 bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300";
  }
  if (verdict === "false_positive") {
    return "border-emerald-300 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300";
  }
  if (verdict === "suspicious" || verdict === "needs_review") {
    return "border-amber-300 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300";
  }
  return "border-border bg-muted text-muted-foreground";
}

function formatPercent(value?: number | null) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "-";
}

export function SocCaseOutcomePanel({
  outcome,
  className,
}: {
  outcome: SocCaseOutcomeView;
  className?: string;
}) {
  const policyHandoffPending = outcome.closure_reason_codes.includes(
    "tenant_policy_handoff_pending",
  );
  const closure = policyHandoffPending
    ? {
        ...CLOSURE.handling_pending,
        label: "研判完成，待按规则转交",
        description: "企业规则要求转交复核，尚未记录转交完成。",
      }
    : CLOSURE[outcome.closure_status];
  const gapImpact = GAP_IMPACT[outcome.evidence_gap_impact];
  const ClosureIcon = closure.icon;
  const handlingLabel = policyHandoffPending
    ? "按企业规则转交复核"
    : outcome.operational_disposition
      ? DISPOSITION_LABELS[outcome.operational_disposition]
      : "尚未执行处置";

  return (
    <section className={cn("overflow-hidden border", className)}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4">
        <div className="flex items-center gap-2">
          <ShieldCheckIcon className="text-muted-foreground size-5" />
          <h3 className="font-semibold">研判处置结果</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className={verdictClass(outcome.security_verdict)}
          >
            安全判断：
            {outcome.security_verdict
              ? VERDICT_LABELS[outcome.security_verdict]
              : "无可用结论"}
          </Badge>
          <Badge variant="outline">处置：{handlingLabel}</Badge>
          <Badge variant="outline" className={closure.className}>
            <ClosureIcon className="size-3.5" />
            {closure.label}
          </Badge>
        </div>
      </div>

      {outcome.handling_reason ? (
        <div className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm leading-6 font-medium text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <Building2Icon className="mt-1 size-4 shrink-0" />
          <p>{outcome.handling_reason}</p>
        </div>
      ) : null}

      <div className="grid divide-y lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)] lg:divide-x lg:divide-y-0">
        <div className="p-5">
          <p className="text-muted-foreground text-xs font-medium">
            发生了什么
          </p>
          <p className="mt-2 text-base leading-7 font-medium">
            {outcome.event_summary}
          </p>
          {outcome.decision_reason ? (
            <p className="text-muted-foreground mt-3 text-sm leading-6">
              {outcome.decision_reason}
            </p>
          ) : null}
          {outcome.change_summary ? (
            <div
              className={cn(
                "mt-4 border-l-4 px-3 py-2 text-sm leading-6",
                outcome.decision_change === "conflicted"
                  ? "border-red-500 bg-red-50 dark:bg-red-950/20"
                  : "border-sky-500 bg-sky-50 dark:bg-sky-950/20",
              )}
            >
              {outcome.change_summary}
            </div>
          ) : null}
        </div>

        <dl className="divide-y p-5 text-sm">
          <div className="flex items-start justify-between gap-4 py-2 first:pt-0">
            <dt className="text-muted-foreground">最终安全判断</dt>
            <dd className="text-right font-medium">
              {outcome.security_verdict
                ? VERDICT_LABELS[outcome.security_verdict]
                : "无可用结论"}
            </dd>
          </div>
          <div className="flex items-start justify-between gap-4 py-2">
            <dt className="text-muted-foreground">运营处置</dt>
            <dd className="text-right font-medium">{handlingLabel}</dd>
          </div>
          <div className="flex items-start justify-between gap-4 py-2">
            <dt className="text-muted-foreground">处理进度</dt>
            <dd className="text-right font-medium">{closure.label}</dd>
          </div>
          <div className="flex items-start justify-between gap-4 py-2 last:pb-0">
            <dt className="text-muted-foreground">研判置信度</dt>
            <dd className="text-right font-medium tabular-nums">
              {formatPercent(outcome.confidence)}
            </dd>
          </div>
          {!outcome.operational_disposition &&
          outcome.handling_recommendation ? (
            <div className="pt-3">
              <dt className="text-muted-foreground">建议下一步处置</dt>
              <dd className="mt-1 leading-6">
                {outcome.handling_recommendation}
              </dd>
            </div>
          ) : null}
        </dl>
      </div>

      {outcome.basis.length ? (
        <div className="border-t px-5 py-4">
          <h4 className="text-sm font-semibold">为什么得到这个结果</h4>
          <div className="mt-3 divide-y border-y">
            {outcome.basis.slice(0, 4).map((item, index) => {
              const BasisIcon = BASIS_ICONS[item.kind];
              return (
                <div
                  key={`${item.kind}-${item.source_id ?? index}`}
                  className="grid gap-2 py-3 md:grid-cols-[11rem_minmax(0,1fr)]"
                >
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <BasisIcon className="text-muted-foreground size-4" />
                    {BASIS_LABELS[item.kind]}
                  </div>
                  <p className="text-muted-foreground text-sm leading-6">
                    {item.summary}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {(outcome.evidence_gaps.length ||
        outcome.evidence_gap_impact !== "none" ||
        outcome.next_steps.length) && (
        <div className="grid border-t lg:grid-cols-2 lg:divide-x">
          <div className="p-5">
            <div className="flex flex-wrap items-center gap-2">
              <ShieldAlertIcon className="size-4" />
              <h4 className="text-sm font-semibold">结论完整性</h4>
              <Badge variant="outline" className={gapImpact.className}>
                {gapImpact.label}
              </Badge>
            </div>
            <p className="text-muted-foreground mt-2 text-sm leading-6">
              {gapImpact.description}
            </p>
            {outcome.blocked_capabilities.length ? (
              <p className="mt-2 text-sm leading-6">
                受限能力：
                {outcome.blocked_capabilities
                  .map((item) => CAPABILITY_LABELS[item])
                  .join("、")}
              </p>
            ) : null}
            {outcome.evidence_gaps.length ? (
              <ul className="mt-3 space-y-1.5 text-sm leading-6">
                {outcome.evidence_gaps.map((item, index) => (
                  <li key={`${index}-${item}`} className="flex gap-2">
                    <span className="text-muted-foreground shrink-0">
                      {index + 1}.
                    </span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="p-5">
            <div className="flex items-center gap-2">
              <Clock3Icon className="text-muted-foreground size-4" />
              <h4 className="text-sm font-semibold">
                {outcome.next_steps.length ? "下一步" : "闭环说明"}
              </h4>
            </div>
            {outcome.next_steps.length ? (
              <ol className="mt-3 space-y-2 text-sm leading-6">
                {outcome.next_steps.map((item, index) => (
                  <li key={`${index}-${item}`} className="flex gap-2">
                    <span className="text-muted-foreground shrink-0">
                      {index + 1}.
                    </span>
                    <span>{item}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-muted-foreground mt-2 text-sm leading-6">
                {closure.description}
              </p>
            )}
          </div>
        </div>
      )}

      {outcome.contributions.length ? (
        <div className="border-t bg-zinc-50/70 px-5 py-4 dark:bg-zinc-950/20">
          <h4 className="text-sm font-semibold">本次系统完成了什么</h4>
          <div className="mt-3 grid gap-x-6 gap-y-2 md:grid-cols-2 xl:grid-cols-4">
            {outcome.contributions.map((item) => (
              <div key={item.kind} className="flex items-start gap-2 text-sm">
                <CheckCircle2Icon className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                <span className="leading-6">{item.summary}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
