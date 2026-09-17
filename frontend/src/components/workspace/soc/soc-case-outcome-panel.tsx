import {
  AlertTriangleIcon,
  ArrowRightIcon,
  CheckCircle2Icon,
  FileSearchIcon,
} from "lucide-react";

import { handlingPresentation } from "@/components/workspace/soc/soc-handling-badge";
import type {
  SocAnalysisCapability,
  SocCaseOutcomeBasisKind,
  SocCaseOutcomeView,
  SocOperationalDisposition,
  SocVerdict,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const VERDICTS: Record<SocVerdict, string> = {
  true_positive: "真实攻击",
  false_positive: "误报 / 无风险",
  suspicious: "可疑",
  unknown: "暂无法判断",
  needs_review: "需要进一步确认",
};
const DISPOSITIONS: Record<SocOperationalDisposition, string> = {
  closed_true_positive: "确认攻击并结案",
  closed_false_positive: "误报结案",
  closed_benign_true_positive: "授权活动结案",
  suppressed: "抑制后续告警",
  escalated: "转交",
  ignored: "忽略",
  duplicate: "合并重复告警",
  unknown: "未指定",
};
const BASIS: Record<SocCaseOutcomeBasisKind, string> = {
  current_analysis: "模型初判依据",
  confirmed_memory: "已审核经验",
  tenant_policy: "企业处置规则",
  external_feedback: "外部处置反馈",
};
const CAPABILITIES: Record<SocAnalysisCapability, string> = {
  scenario_routing: "场景路由",
  network_direction: "网络方向",
  source_targeting: "来源目标",
  destination_targeting: "目的目标",
  attacker_targeting: "攻击者目标",
  victim_targeting: "受害者目标",
  impacted_asset_targeting: "受影响资产",
  user_targeting: "账号目标",
  response_action: "自动响应",
};

function Notes({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <h4 className="text-sm font-medium">{title}</h4>
      <ul className="text-muted-foreground mt-2 space-y-2 text-sm leading-6 break-words">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export function SocCaseOutcomePanel({
  outcome,
  className,
}: {
  outcome: SocCaseOutcomeView;
  className?: string;
}) {
  const failed = outcome.closure_status === "failed";
  const handling = handlingPresentation(outcome.recommended_handling, failed);
  const Icon = handling.icon;
  const support = outcome.conclusion_support;
  const blocking = outcome.evidence_gap_impact === "decision_blocking";
  const executionIssue = outcome.closure_reason_codes.some((code) =>
    ["action_execution_failed", "action_execution_skipped"].includes(code),
  );
  const reason = failed
    ? (outcome.progress_detail ?? outcome.event_summary)
    : (outcome.handling_reason ??
      outcome.decision_reason ??
      outcome.event_summary);
  // These are persisted service/model suggestions, not new browser-owned tasks.
  const steps = [
    ...new Set([
      ...(!failed &&
      !blocking &&
      !executionIssue &&
      outcome.recommended_handling === "transfer" &&
      outcome.handling_recommendation
        ? [outcome.handling_recommendation]
        : []),
      ...outcome.next_steps,
    ]),
  ];
  const showSteps =
    steps.length > 0 &&
    (failed ||
      blocking ||
      executionIssue ||
      outcome.recommended_handling !== "ignore");
  const directHandling =
    outcome.recommended_handling === "transfer"
      ? "直接转交，"
      : outcome.recommended_handling === "ignore"
        ? "直接忽略，"
        : "";

  return (
    <section className={cn("border", className)} aria-label="处理结论">
      <div
        className={cn(
          "flex items-center gap-3 border-b px-5 py-4",
          handling.className,
        )}
      >
        <Icon className="size-6 shrink-0" />
        <h3 className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-sm font-medium">处理结论</span>
          <span className="text-xl font-semibold">{handling.label}</span>
        </h3>
      </div>
      <div
        className={cn(
          "grid",
          showSteps &&
            "lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] lg:divide-x",
        )}
      >
        <div className="min-w-0 p-5">
          {!failed && outcome.memory_matching_facts?.length ? (
            <div
              className="mb-4 border-b pb-4"
              data-testid="memory-matching-facts"
            >
              <Notes
                title="系统匹配说明"
                items={outcome.memory_matching_facts}
              />
            </div>
          ) : null}
          <h4 className="text-muted-foreground text-xs font-medium">
            {outcome.memory_matching_facts?.length &&
            outcome.processing_path === "model_analysis" &&
            !outcome.tenant_policy_applied &&
            !outcome.memory_directive_applied
              ? "模型研判依据"
              : "处理依据"}
          </h4>
          <p className="mt-2 text-sm leading-7 break-words">{reason}</p>
          {outcome.processing_path &&
          outcome.processing_path !== "model_analysis" ? (
            <p className="mt-3 flex items-center gap-2 text-sm font-medium text-emerald-700">
              <CheckCircle2Icon className="size-4 shrink-0" />
              {outcome.processing_path === "tenant_policy"
                ? `命中企业规则 · ${directHandling}未调用主模型`
                : "精确匹配审核经验 · 直接复用结论，未调用主模型"}
            </p>
          ) : null}
          {support?.resolved_questions.length ? (
            <div className="mt-4 flex items-start gap-2">
              <CheckCircle2Icon className="mt-1 size-4 shrink-0 text-emerald-700" />
              <Notes
                title="经验已解释的问题"
                items={support.resolved_questions}
              />
            </div>
          ) : null}
          {!failed && (blocking || executionIssue) ? (
            <div
              role="note"
              className="mt-4 flex items-start gap-2 border-l-2 border-amber-500 pl-3 text-sm leading-6"
            >
              <AlertTriangleIcon className="mt-1 size-4 shrink-0" />
              <div>
                <p className="font-medium">
                  {outcome.progress_label ?? "需处理的问题"}
                </p>
                <p className="text-muted-foreground">
                  {outcome.progress_detail}
                </p>
              </div>
            </div>
          ) : null}
        </div>
        {showSteps ? (
          <div className="min-w-0 border-t p-5 lg:border-t-0">
            <h4 className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
              <ArrowRightIcon className="size-4" />
              下一步
            </h4>
            <ol className="mt-2 space-y-2 text-sm leading-7 break-words">
              {steps.map((item, index) => (
                <li className="flex gap-2" key={index}>
                  <span className="text-muted-foreground shrink-0 tabular-nums">
                    {index + 1}.
                  </span>
                  <span className="min-w-0">{item}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </div>

      <details className="border-t" data-testid="outcome-details">
        <summary className="cursor-pointer px-5 py-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2">
          研判与处置详情
        </summary>
        <div className="space-y-5 border-t p-5">
          <dl className="grid gap-x-8 gap-y-4 text-sm md:grid-cols-2 xl:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">风险判断</dt>
              <dd className="mt-1">
                {outcome.processing_path === "tenant_policy"
                  ? "按企业规则直接确定处置，未进行模型风险研判"
                  : outcome.security_verdict
                    ? VERDICTS[outcome.security_verdict]
                    : "未形成判断"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">研判置信度</dt>
              <dd className="mt-1 tabular-nums">
                {typeof outcome.confidence === "number"
                  ? Math.round(outcome.confidence * 100) + "%"
                  : "-"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">具体处置方案</dt>
              <dd className="mt-1">
                {outcome.operational_disposition
                  ? DISPOSITIONS[outcome.operational_disposition]
                  : "未指定"}
              </dd>
            </div>
            <div className="md:col-span-2 xl:col-span-3">
              <dt className="text-muted-foreground">执行与反馈</dt>
              <dd className="mt-1 leading-6">
                {outcome.progress_label ?? "未记录处理进度"}
                {outcome.progress_detail ? "：" + outcome.progress_detail : ""}
              </dd>
            </div>
          </dl>
          <Notes title="原始研判摘要" items={[outcome.event_summary]} />
          {outcome.change_summary ? (
            <Notes title="判断调整记录" items={[outcome.change_summary]} />
          ) : null}
          {outcome.basis.length ? (
            <div className="divide-y border-y">
              {outcome.basis.map((item, index) => (
                <div
                  key={index}
                  className="grid gap-2 py-3 md:grid-cols-[10rem_minmax(0,1fr)]"
                >
                  <h4 className="text-sm font-medium">{BASIS[item.kind]}</h4>
                  <p className="text-muted-foreground text-sm leading-6 break-words">
                    {item.summary}
                  </p>
                </div>
              ))}
            </div>
          ) : null}
          <Notes
            title={blocking ? "原始研判疑点" : "补充信息，不影响当前结论"}
            items={outcome.evidence_gaps}
          />
          <Notes
            title="经验复用前的原始疑点"
            items={outcome.prior_analysis_gaps}
          />
          <Notes
            title="可选补充，不是必做任务"
            items={support?.optional_checks}
          />
          <Notes
            title="何时需要重新研判"
            items={support?.reassessment_triggers}
          />
          {outcome.blocked_capabilities.length ? (
            <Notes
              title={
                outcome.action_limits_relevant
                  ? "动作能力限制"
                  : "未用于本次动作的能力检查"
              }
              items={[
                outcome.blocked_capabilities
                  .map((item) => CAPABILITIES[item])
                  .join("、"),
              ]}
            />
          ) : null}
          {!showSteps ? <Notes title="处置建议记录" items={steps} /> : null}
          {outcome.contributions.length ? (
            <div>
              <h4 className="flex items-center gap-2 text-sm font-medium">
                <FileSearchIcon className="size-4" />
                本次系统处理记录
              </h4>
              <ul className="text-muted-foreground mt-2 space-y-2 text-sm leading-6">
                {outcome.contributions.map((item) => (
                  <li key={item.kind}>{item.summary}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </details>
    </section>
  );
}
