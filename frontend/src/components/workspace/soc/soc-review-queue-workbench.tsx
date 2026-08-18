"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ClipboardCheckIcon,
  CircleIcon,
  FlaskConicalIcon,
  InboxIcon,
  KeyRoundIcon,
  LibraryBigIcon,
  PencilIcon,
  PlayCircleIcon,
  PowerIcon,
  RefreshCwIcon,
  SearchCheckIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  SparklesIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  SocDispositionSampleInbox,
  type SocDispositionSampleReviewTarget,
} from "@/components/workspace/soc/soc-disposition-sample-inbox";
import {
  useCloseSocReviewItem,
  useCorrectSocReviewRun,
  useCreateSocApprovalGrant,
  useDryRunSocApprovedAction,
  useDraftSocMemoryBusinessLesson,
  useExpireSocApprovalRequest,
  useExecuteSocApprovedAction,
  useRejectSocApprovalRequest,
  useReviewSocMemoryCandidate,
  useRecordSocDispositionOutcome,
  useSocApprovalRequest,
  useSocApprovalRequests,
  useSocMemoryRecords,
  useSocMemoryCandidate,
  useSocMemoryCandidates,
  useSocReviewContext,
  useSocReviewItems,
  useUpdateSocMemoryRetrievalActivation,
} from "@/core/soc";
import type {
  SocAgentActionResult,
  SocAgentApprovalGrant,
  SocAgentApprovalRequest,
  SocAgentApprovedActionCommand,
  SocAuthorizationEnrichmentRecord,
  SocDispositionOutcomeRecord,
  SocDispositionOutcomeReviewKind,
  SocDispositionProposalRecord,
  SocExternalDispositionRecord,
  SocInvestigationEvidence,
  SocInvestigationTimelineItem,
  SocMemoryCandidate,
  SocMemoryCandidateReviewDecision,
  SocMemoryApplicabilitySpec,
  SocMemoryBusinessLesson,
  SocMemoryRecord,
  SocMemoryRetrievalActivationAction,
  SocMemoryRetrievalResult,
  SocOperationalDisposition,
  SocReviewQueueItem,
  SocReviewQueueStatus,
  SocUnifiedInvestigationView,
  SocVerdict,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const STATUS_OPTIONS: { value: SocReviewQueueStatus | "all"; label: string }[] =
  [
    { value: "open", label: "待复核" },
    { value: "closed", label: "已关闭" },
    { value: "all", label: "全部" },
  ];

const VERDICT_OPTIONS: { value: SocVerdict; label: string }[] = [
  { value: "true_positive", label: "真实攻击" },
  { value: "suspicious", label: "可疑" },
  { value: "false_positive", label: "误报" },
  { value: "unknown", label: "未知" },
  { value: "needs_review", label: "需复核" },
];

const DISPOSITION_OPTIONS: {
  value: SocOperationalDisposition;
  label: string;
}[] = [
  { value: "closed_true_positive", label: "真实攻击关闭" },
  { value: "closed_false_positive", label: "误报关闭" },
  { value: "closed_benign_true_positive", label: "真实但已授权" },
  { value: "suppressed", label: "已抑制" },
  { value: "escalated", label: "已升级" },
  { value: "ignored", label: "已忽略" },
  { value: "duplicate", label: "重复事件" },
  { value: "unknown", label: "证据不足" },
];

interface MemoryRetrievalDraft {
  reason: string;
  validUntil: string;
  reviewAfterDays: string;
}

interface MemoryCandidateReviewDraft {
  businessContext: string;
  applyToFutureMatches: boolean;
  confirmedVerdict: SocVerdict | null;
  promotedFacetKeys: string[];
  lessonConclusion: string;
  lessonBusinessRationale: string;
  lessonGeneralizationBoundary: string;
  lessonInvalidationCondition: string;
  lessonHandlingGuidance: string;
  lessonDraftProvenance: string;
  lessonDraftUncertainties: string[];
  lessonEditing: boolean;
}

function defaultMemoryCandidateReviewDraft(
  _candidate: SocMemoryCandidate,
): MemoryCandidateReviewDraft {
  return {
    businessContext: "",
    applyToFutureMatches: false,
    confirmedVerdict: null,
    promotedFacetKeys: [],
    lessonConclusion: "",
    lessonBusinessRationale: "",
    lessonGeneralizationBoundary: "",
    lessonInvalidationCondition: "",
    lessonHandlingGuidance: "",
    lessonDraftProvenance: "",
    lessonDraftUncertainties: [],
    lessonEditing: false,
  };
}

function reviewedMemoryApplicability(
  candidate: SocMemoryCandidate,
  draft: MemoryCandidateReviewDraft,
): SocMemoryApplicabilitySpec | undefined {
  const base = candidate.applicability;
  if (!base || draft.promotedFacetKeys.length === 0) return undefined;
  const promoted = draft.promotedFacetKeys.filter(
    (key) => base.optional_facets[key] !== undefined,
  );
  if (promoted.length === 0) return undefined;
  const optionalFacets = Object.fromEntries(
    Object.entries(base.optional_facets).filter(
      ([key]) => !promoted.includes(key),
    ),
  );
  return {
    ...base,
    required_facets: {
      ...base.required_facets,
      ...Object.fromEntries(
        promoted.map((key) => [key, base.optional_facets[key]!]),
      ),
    },
    optional_facets: optionalFacets,
    context_only_required_facet_keys:
      base.context_only_required_facet_keys.length > 0
        ? Array.from(
            new Set([...base.context_only_required_facet_keys, ...promoted]),
          ).sort()
        : [],
  };
}

function reviewedLessonItems(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function hasMemoryLessonDraft(draft: MemoryCandidateReviewDraft) {
  return [
    draft.lessonConclusion,
    draft.lessonBusinessRationale,
    draft.lessonGeneralizationBoundary,
    draft.lessonInvalidationCondition,
    draft.lessonHandlingGuidance,
  ].some((value) => value.trim().length > 0);
}

function reviewedMemoryBusinessLesson(
  draft: MemoryCandidateReviewDraft,
  applicability: SocMemoryApplicabilitySpec | null | undefined,
): SocMemoryBusinessLesson | undefined {
  const conclusion = draft.lessonConclusion.trim();
  const businessRationale = reviewedLessonItems(draft.lessonBusinessRationale);
  const generalizationBoundaries = reviewedLessonItems(
    draft.lessonGeneralizationBoundary,
  );
  const invalidationConditions = reviewedLessonItems(
    draft.lessonInvalidationCondition,
  );
  const handlingGuidance = reviewedLessonItems(draft.lessonHandlingGuidance);
  const applicabilityConditions = applicability
    ? Object.entries(applicability.required_facets).map(
        ([key, values]) =>
          `Required canonical facet ${key}: ${values.join(", ")}`,
      )
    : [];
  if (applicability?.minimum_optional_matches) {
    applicabilityConditions.push(
      `At least ${applicability.minimum_optional_matches} reviewed optional facet groups must also match.`,
    );
  }
  if (
    conclusion.length < 10 ||
    businessRationale.length === 0 ||
    businessRationale.some((item) => item.length < 5) ||
    applicabilityConditions.length === 0 ||
    generalizationBoundaries.length === 0 ||
    generalizationBoundaries.some((item) => item.length < 5) ||
    invalidationConditions.length === 0 ||
    invalidationConditions.some((item) => item.length < 5) ||
    handlingGuidance.length === 0 ||
    handlingGuidance.some((item) => item.length < 5)
  ) {
    return undefined;
  }
  return {
    schema_version: "soc.memory_business_lesson.v1",
    conclusion,
    business_rationale: businessRationale,
    applicability_conditions: applicabilityConditions,
    generalization_boundaries: generalizationBoundaries,
    invalidation_conditions: invalidationConditions,
    handling_guidance: handlingGuidance,
  };
}

function defaultMemoryRetrievalDraft(): MemoryRetrievalDraft {
  const validUntil = new Date();
  validUntil.setDate(validUntil.getDate() + 90);
  return {
    reason: "",
    validUntil: validUntil.toISOString().slice(0, 16),
    reviewAfterDays: "30",
  };
}

const OUTCOME_REVIEW_KIND_OPTIONS: {
  value: SocDispositionOutcomeReviewKind;
  label: string;
}[] = [
  { value: "analyst_resolution", label: "分析师结论" },
  { value: "sampled_quality_review", label: "独立抽样复核" },
];

const DEFAULT_APPROVAL_REQUEST_JSON = JSON.stringify(
  {
    permission_decision_id: "PERM-MANUAL-001",
    route: "response.block_ip",
    action: "response.block_ip",
    risk_level: "high_risk",
    reason: "high-risk response action requires human approval",
    requested_by: {
      actor_id: "soc-agent",
      surface: "web",
      roles: ["analyst"],
    },
  },
  null,
  2,
);

function formatTime(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatPercent(value: number | null | undefined) {
  if (typeof value !== "number") return "-";
  return `${Math.round(value * 100)}%`;
}

function prettyJson(value: unknown) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return Object.prototype.toString.call(value);
  }
}

function priorityClass(priority: SocReviewQueueItem["priority"]) {
  if (priority === "high") return "border-red-200 bg-red-50 text-red-700";
  if (priority === "medium") {
    return "border-amber-200 bg-amber-50 text-amber-700";
  }
  return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

function verdictLabel(value: string | null | undefined) {
  return VERDICT_OPTIONS.find((item) => item.value === value)?.label ?? "未知";
}

function queueItemLabel(item: SocReviewQueueItem) {
  return item.rule_name ?? item.rule_code ?? item.alert_id;
}

function approvalRequestLabel(request: SocAgentApprovalRequest) {
  return `${request.action} / ${request.approval_request_id ?? request.permission_decision_id}`;
}

function hasObjectEntries(value: Record<string, unknown> | null | undefined) {
  return !!value && Object.keys(value).length > 0;
}

function candidateSourceLabel(candidate: SocMemoryCandidate) {
  const source = candidate.source;
  const refs = [
    source.source_type,
    source.run_id ? `run ${source.run_id}` : null,
    source.alert_id ? `alert ${source.alert_id}` : null,
    source.queue_id ? `queue ${source.queue_id}` : null,
  ].filter(Boolean);
  return refs.join(" / ");
}

function candidateStatusLabel(status: SocMemoryCandidate["status"]) {
  const labels: Partial<Record<SocMemoryCandidate["status"], string>> = {
    pending_review: "待审核",
    confirmed_candidate: "已确认候选",
    confirmed: "已确认",
    rejected: "已驳回",
    expired: "已过期",
    deprecated: "已停用",
  };
  return labels[status] ?? status;
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function candidateCohortMetrics(candidate: SocMemoryCandidate) {
  const cohortQuality = objectRecord(candidate.metadata.cohort_quality);
  return {
    supportCount:
      finiteNumber(candidate.metadata.support_count_at_creation) ??
      finiteNumber(cohortQuality?.support_count),
    distinctSourceCount:
      finiteNumber(candidate.metadata.distinct_source_count_at_creation) ??
      finiteNumber(cohortQuality?.distinct_source_count),
    consistencyRatio:
      finiteNumber(cohortQuality?.consistency_ratio) ?? candidate.confidence,
    riskClass:
      typeof cohortQuality?.dominant_risk_class === "string"
        ? cohortQuality.dominant_risk_class
        : null,
  };
}

function candidateRiskClassLabel(value: string | null) {
  if (value === "risk") return "有风险候选";
  if (value === "benign") return "无风险候选";
  if (value === "mixed") return "结论冲突";
  return "待人工判断";
}

function candidateScopeHighlights(candidate: SocMemoryCandidate) {
  const definitions = [
    { key: "rule_code", label: "规则" },
    { key: "product", label: "产品" },
    { key: "environment", label: "环境" },
    { key: "behavior_component", label: "行为" },
  ];
  return definitions.flatMap(({ key, label }) => {
    const values = candidate.facets[key] ?? [];
    return values.length > 0 ? [{ label, values: values.slice(0, 3) }] : [];
  });
}

function CandidateMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-r border-b px-3 py-2 even:border-r-0 lg:border-b-0 lg:last:border-r-0 lg:even:border-r [&:nth-last-child(-n+2)]:border-b-0">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 truncate text-sm font-medium">{value}</div>
    </div>
  );
}

const MEMORY_LESSON_BLUEPRINT = [
  {
    key: "conclusion",
    order: "01",
    title: "经验结论",
    english: "Conclusion",
    emphasis: "核心判断",
    description: "说明这个模式的业务含义，以及最终判定有无风险。",
  },
  {
    key: "business_rationale",
    order: "02",
    title: "业务依据",
    english: "Business rationale",
    emphasis: "事实基础",
    description: "记录运营专家确认过的事实，解释为什么可以得出该结论。",
  },
  {
    key: "applicability_conditions",
    order: "03",
    title: "适用条件",
    english: "Applicability",
    emphasis: "匹配开关",
    description:
      "决定什么样的新告警有资格命中这条 Memory，由系统从候选范围生成。",
  },
  {
    key: "generalization_boundaries",
    order: "04",
    title: "泛化边界",
    english: "Generalization",
    emphasis: "允许变化",
    description: "说明主机、IP、时间或实例等哪些差异不会改变业务结论。",
  },
  {
    key: "invalidation_conditions",
    order: "05",
    title: "失效条件",
    english: "Invalidation",
    emphasis: "安全边界",
    description: "列出出现哪些反证或变化后必须停止复用，并重新研判。",
  },
  {
    key: "handling_guidance",
    order: "06",
    title: "处置建议",
    english: "Handling guidance",
    emphasis: "后续动作",
    description: "说明精确命中后如何处置；文字本身不直接授予自动执行权限。",
  },
] as const;

function MemoryLessonReadView({ lesson }: { lesson: SocMemoryBusinessLesson }) {
  const values: Record<
    (typeof MEMORY_LESSON_BLUEPRINT)[number]["key"],
    string[]
  > = {
    conclusion: [lesson.conclusion],
    business_rationale: lesson.business_rationale,
    applicability_conditions: lesson.applicability_conditions,
    generalization_boundaries: lesson.generalization_boundaries,
    invalidation_conditions: lesson.invalidation_conditions,
    handling_guidance: lesson.handling_guidance,
  };

  return (
    <div className="divide-y border-y">
      {MEMORY_LESSON_BLUEPRINT.map((item) => (
        <div
          key={item.key}
          className="grid min-w-0 gap-2 py-3 lg:grid-cols-[12rem_minmax(0,1fr)]"
        >
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-muted-foreground font-mono text-xs">
                {item.order}
              </span>
              <span className="text-xs font-semibold">{item.title}</span>
              <Badge variant="outline" className="text-[10px]">
                {item.emphasis}
              </Badge>
            </div>
            <p className="text-muted-foreground mt-1 text-xs">
              {item.description}
            </p>
          </div>
          {item.key === "conclusion" ? (
            <p className="text-sm leading-6 break-words">
              {values[item.key][0]}
            </p>
          ) : (
            <ul className="space-y-1 text-sm leading-6">
              {values[item.key].map((value) => (
                <li key={value} className="break-words">
                  {value}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

function timelineKindLabel(kind: SocInvestigationTimelineItem["kind"]) {
  const labels: Record<SocInvestigationTimelineItem["kind"], string> = {
    analysis: "分析",
    decision: "决策",
    correlation: "关联",
    domain_finding: "领域发现",
    read_only_evidence: "只读证据",
    investigation_addendum: "调查附录",
    authorization_enrichment: "授权上下文",
    disposition_proposal: "影子处置建议",
    disposition_outcome: "影子评测结果",
    external_disposition: "外部反馈",
    memory_candidate: "候选记忆",
    relevant_memory: "确认记忆",
    audit: "审计",
    correction: "纠正",
  };
  return labels[kind];
}

function UnifiedInvestigationViewSection({
  view,
}: {
  view: SocUnifiedInvestigationView | null | undefined;
}) {
  if (!view) {
    return (
      <section className="rounded-md border">
        <div className="text-muted-foreground p-4 text-sm">
          当前上下文没有统一调查视图。
        </div>
      </section>
    );
  }
  const correlationMatches = view.correlation_result?.matches ?? [];
  const domainFindings = view.domain_triage_results.flatMap(
    (result) => result.findings,
  );
  const timeline = view.evidence_timeline.slice(0, 8);
  const latestAddendum = (view.investigation_addenda ?? [])[0];
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <SearchCheckIcon className="text-muted-foreground size-4" />
            <h3 className="text-sm font-semibold">统一调查视图</h3>
          </div>
          <p className="text-muted-foreground mt-1 text-xs">
            {view.primary_summary ?? "暂无研判摘要"}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Badge variant="outline">{verdictLabel(view.runtime_verdict)}</Badge>
          <Badge variant="secondary">
            confidence {formatPercent(view.runtime_confidence)}
          </Badge>
          {view.needs_review ? (
            <Badge variant="outline">needs review</Badge>
          ) : null}
        </div>
      </div>

      <div className="grid divide-y text-sm md:grid-cols-4 md:divide-x md:divide-y-0">
        <div className="p-4">
          <div className="text-muted-foreground text-xs">只读证据</div>
          <div className="mt-1 text-lg font-semibold">
            {view.counts.action_evidence ?? 0}
          </div>
        </div>
        <div className="p-4">
          <div className="text-muted-foreground text-xs">关联告警</div>
          <div className="mt-1 text-lg font-semibold">
            {view.counts.correlation_matches ?? correlationMatches.length}
          </div>
        </div>
        <div className="p-4">
          <div className="text-muted-foreground text-xs">领域发现</div>
          <div className="mt-1 text-lg font-semibold">
            {view.counts.domain_findings ?? domainFindings.length}
          </div>
        </div>
        <div className="p-4">
          <div className="text-muted-foreground text-xs">记忆命中</div>
          <div className="mt-1 text-lg font-semibold">
            {view.counts.relevant_memories ?? 0}
          </div>
        </div>
      </div>

      {latestAddendum ? (
        <div className="border-t px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-medium">只读调查附录</div>
              <p className="text-muted-foreground mt-1 text-xs">
                {latestAddendum.summary}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">{latestAddendum.execution_status}</Badge>
              <Badge variant="secondary">
                evidence {formatPercent(latestAddendum.evidence_coverage_ratio)}
              </Badge>
              {latestAddendum.analyst_attention_required ? (
                <Badge variant="outline">attention required</Badge>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      <div className="grid border-t xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="border-b p-4 xl:border-r xl:border-b-0">
          <div className="mb-3 text-sm font-medium">领域发现</div>
          {domainFindings.length === 0 ? (
            <div className="text-muted-foreground text-sm">暂无领域发现。</div>
          ) : (
            <div className="space-y-3">
              {domainFindings.slice(0, 4).map((finding) => (
                <div
                  key={finding.finding_id}
                  className="border-b pb-3 last:border-b-0 last:pb-0"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0 text-sm font-medium">
                      {finding.title}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline">{finding.domain}</Badge>
                      {finding.scenario_key ? (
                        <Badge variant="outline">{finding.scenario_key}</Badge>
                      ) : null}
                      <Badge variant="secondary">{finding.disposition}</Badge>
                      <Badge variant="outline">
                        {formatPercent(finding.confidence)}
                      </Badge>
                    </div>
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {finding.summary}
                  </p>
                  <p className="mt-2 text-xs">
                    {finding.current_conclusion.summary}
                  </p>
                  {finding.evidence_profile.gaps.length > 0 ? (
                    <p className="text-muted-foreground mt-1 text-xs">
                      证据缺口：
                      {finding.evidence_profile.gaps.slice(0, 2).join("；")}
                    </p>
                  ) : null}
                  {finding.recommendations.length > 0 ? (
                    <p className="text-muted-foreground mt-2 text-xs">
                      {finding.recommendations[0]}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="p-4">
          <div className="mb-3 text-sm font-medium">调查时间线</div>
          {timeline.length === 0 ? (
            <div className="text-muted-foreground text-sm">
              暂无时间线事件。
            </div>
          ) : (
            <div className="space-y-3">
              {timeline.map((item) => (
                <div
                  key={item.item_id}
                  className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-3 border-b pb-3 last:border-b-0 last:pb-0"
                >
                  <div className="text-muted-foreground text-xs">
                    <div>{timelineKindLabel(item.kind)}</div>
                    <div>{formatTime(item.occurred_at)}</div>
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="truncate text-sm font-medium">
                        {item.title}
                      </div>
                      {item.status ? (
                        <Badge variant="outline">{item.status}</Badge>
                      ) : null}
                    </div>
                    <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                      {item.summary ?? "-"}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {correlationMatches.length > 0 ? (
        <div className="border-t p-4">
          <div className="mb-3 text-sm font-medium">Top 关联告警</div>
          <div className="divide-y">
            {correlationMatches.slice(0, 3).map((match) => {
              const reasonText = match.matched_reasons.join(", ");
              const description =
                reasonText.length > 0
                  ? reasonText
                  : (match.summary.summary ?? "-");
              return (
                <div
                  key={match.summary.run_id}
                  className="py-3 first:pt-0 last:pb-0"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0 truncate text-sm font-medium">
                      {match.summary.rule_name ??
                        match.summary.rule_code ??
                        match.summary.alert_id}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="secondary">
                        score {match.score.toFixed(0)}
                      </Badge>
                      <Badge variant="outline">
                        evidence {match.reusable_evidence.length}
                      </Badge>
                    </div>
                  </div>
                  <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                    {description}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function ApprovalProposalSummary({
  request,
}: {
  request: SocAgentApprovalRequest | null;
}) {
  if (!request) return null;
  const hasProposalDetails =
    !!request.source_proposal_id ||
    hasObjectEntries(request.action_payload) ||
    hasObjectEntries(request.context_refs);
  if (!hasProposalDetails) return null;

  return (
    <div className="bg-muted/30 rounded-md border p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="text-sm font-medium">Lead Agent proposal</div>
        {request.source_proposal_id ? (
          <Badge variant="outline">{request.source_proposal_id}</Badge>
        ) : null}
      </div>
      <div className="grid gap-3 text-xs lg:grid-cols-2">
        <div className="space-y-1">
          <div className="text-muted-foreground">Action payload</div>
          <pre className="bg-background max-h-36 overflow-auto rounded border p-2 whitespace-pre-wrap">
            {prettyJson(request.action_payload ?? {})}
          </pre>
        </div>
        <div className="space-y-1">
          <div className="text-muted-foreground">Context refs</div>
          <pre className="bg-background max-h-36 overflow-auto rounded border p-2 whitespace-pre-wrap">
            {prettyJson(request.context_refs ?? {})}
          </pre>
        </div>
      </div>
    </div>
  );
}

function ActionEvidenceSection({
  evidence,
}: {
  evidence: SocInvestigationEvidence[];
}) {
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <SearchCheckIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">只读查询证据</h3>
        </div>
        <Badge variant="secondary">{evidence.length}</Badge>
      </div>
      <div className="divide-y">
        {evidence.length === 0 ? (
          <div className="text-muted-foreground p-4 text-sm">
            当前工单还没有资产查询、定位或其他只读工具结果。
          </div>
        ) : (
          evidence.map((item) => (
            <div key={item.evidence_id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {item.action}
                  </div>
                  <div className="text-muted-foreground mt-1 text-xs">
                    {item.route} / {formatTime(item.created_at)}
                  </div>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <Badge variant="outline">{item.status}</Badge>
                  {item.source_proposal_id ? (
                    <Badge variant="secondary">proposal</Badge>
                  ) : null}
                </div>
              </div>
              <p className="text-muted-foreground mt-2 text-xs">
                {item.message}
              </p>
              <div className="text-muted-foreground mt-2 text-xs">
                {item.actor?.actor_id ? `actor: ${item.actor.actor_id}` : null}
                {item.thread_id ? ` / thread: ${item.thread_id}` : null}
                {item.source_proposal_id
                  ? ` / proposal: ${item.source_proposal_id}`
                  : null}
              </div>
              <pre className="bg-muted mt-3 max-h-48 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                {prettyJson(item.result_payload)}
              </pre>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function AuthorizationEnrichmentSection({
  records,
}: {
  records: SocAuthorizationEnrichmentRecord[];
}) {
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <ShieldCheckIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">授权活动匹配</h3>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{records.length}</Badge>
          <Badge variant="outline">shadow only</Badge>
        </div>
      </div>
      <div className="divide-y">
        {records.length === 0 ? (
          <div className="text-muted-foreground p-4 text-sm">
            当前运行还没有持久化的授权活动匹配记录。
          </div>
        ) : (
          records.map((record) => {
            const result = record.match_result;
            return (
              <div key={record.enrichment_id} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">
                      {record.enrichment_id}
                    </div>
                    <div className="text-muted-foreground mt-1 text-xs">
                      {record.query.tenant_id ?? "tenant unknown"} /{" "}
                      {record.query.environment ?? "environment unknown"} /{" "}
                      {formatTime(record.created_at)}
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    <Badge variant="outline">{result.status}</Badge>
                    <Badge variant="secondary">
                      facts {result.matched_fact_refs.length}
                    </Badge>
                    {record.replay_of_enrichment_id ? (
                      <Badge variant="outline">replay</Badge>
                    ) : null}
                  </div>
                </div>
                <div className="text-muted-foreground mt-3 grid gap-1 text-xs sm:grid-cols-2">
                  <span>policy {record.matcher_policy_version}</span>
                  <span>decision impact {record.decision_impact}</span>
                  <span>
                    matched {result.matched_dimensions.join(", ") || "-"}
                  </span>
                  <span>
                    missing {result.missing_dimensions.join(", ") || "-"}
                  </span>
                </div>
                {result.matched_fact_refs.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {result.matched_fact_refs.map((fact) => (
                      <Badge key={fact.fact_version_id} variant="outline">
                        {fact.fact_id} v{fact.version}
                      </Badge>
                    ))}
                  </div>
                ) : null}
                {result.warnings.length > 0 ? (
                  <p className="text-muted-foreground mt-3 text-xs">
                    {result.warnings.slice(0, 3).join("; ")}
                  </p>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function DispositionProposalSection({
  proposals,
}: {
  proposals: SocDispositionProposalRecord[];
}) {
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <ClipboardCheckIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">影子处置建议</h3>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{proposals.length}</Badge>
          <Badge variant="outline">人工复核</Badge>
        </div>
      </div>
      <div className="divide-y">
        {proposals.length === 0 ? (
          <div className="text-muted-foreground p-4 text-sm">
            当前运行还没有满足确定性策略的影子处置建议。
          </div>
        ) : (
          proposals.map((proposal) => (
            <div key={proposal.proposal_id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {proposal.proposed_disposition}
                  </div>
                  <div className="text-muted-foreground mt-1 text-xs">
                    {proposal.proposal_id} / {formatTime(proposal.created_at)}
                  </div>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <Badge variant="outline">
                    detection {proposal.detection_truth.verdict}
                  </Badge>
                  <Badge variant="secondary">shadow only</Badge>
                  <Badge variant="outline">not applied</Badge>
                </div>
              </div>
              <p className="text-muted-foreground mt-3 text-xs">
                {proposal.rationale.join(" ")}
              </p>
              <div className="text-muted-foreground mt-3 grid gap-1 text-xs sm:grid-cols-2">
                <span>reason {proposal.reason_code}</span>
                <span>policy {proposal.policy_version}</span>
                <span>source {proposal.source_enrichment_id}</span>
                <span>fact versions {proposal.source_fact_refs.length}</span>
                <span>auto close {String(proposal.auto_close_allowed)}</span>
                <span>review impact {proposal.review_queue_impact}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function DispositionOutcomeCaptureSection({
  queueStatus,
  proposals,
  outcomes,
  prefill,
}: {
  queueStatus: SocReviewQueueStatus;
  proposals: SocDispositionProposalRecord[];
  outcomes: SocDispositionOutcomeRecord[];
  prefill?: SocDispositionSampleReviewTarget | null;
}) {
  const [proposalId, setProposalId] = useState("");
  const [observedDisposition, setObservedDisposition] =
    useState<SocOperationalDisposition>("closed_benign_true_positive");
  const [reviewKind, setReviewKind] =
    useState<SocDispositionOutcomeReviewKind>("analyst_resolution");
  const [sampleId, setSampleId] = useState("");
  const [reason, setReason] = useState("");
  const mutation = useRecordSocDispositionOutcome();

  const activeProposal =
    proposals.find((proposal) => proposal.proposal_id === proposalId) ??
    proposals[0];
  const activeProposalId = activeProposal?.proposal_id ?? "";
  const proposedDisposition = activeProposal?.proposed_disposition;
  const prefillAvailable =
    !!prefill &&
    proposals.some((proposal) => proposal.proposal_id === prefill.proposalId);
  useEffect(() => {
    if (!prefill || !prefillAvailable) return;
    setProposalId(prefill.proposalId);
    setReviewKind("sampled_quality_review");
    setSampleId(prefill.sampleId);
  }, [prefill, prefillAvailable]);
  useEffect(() => {
    if (proposedDisposition) {
      setObservedDisposition(proposedDisposition);
    }
  }, [activeProposalId, proposedDisposition]);
  const latestOutcome = outcomes.find(
    (outcome) =>
      outcome.proposal_id === activeProposalId &&
      outcome.review_kind === reviewKind,
  );
  const sampled = reviewKind === "sampled_quality_review";
  const canSubmit =
    queueStatus === "closed" &&
    activeProposalId.length > 0 &&
    reason.trim().length > 0 &&
    (!sampled || sampleId.trim().length > 0);

  const handleSubmit = async () => {
    if (!canSubmit) return;
    try {
      await mutation.mutateAsync({
        proposal_id: activeProposalId,
        observed_disposition: observedDisposition,
        review_kind: reviewKind,
        sample_id: sampled ? sampleId.trim() : null,
        reason: reason.trim(),
        evidence_refs: [],
        supersedes_outcome_id: latestOutcome?.outcome_id ?? null,
      });
      setReason("");
      toast.success(latestOutcome ? "处置标签修订已记录" : "处置标签已记录");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "处置标签记录失败");
    }
  };

  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <ClipboardCheckIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">结构化处置标签</h3>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline">append-only</Badge>
          <Badge variant="secondary">shadow evaluation</Badge>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-2">
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="outcome-proposal">
            影子建议
          </label>
          <Select value={activeProposalId} onValueChange={setProposalId}>
            <SelectTrigger id="outcome-proposal" className="w-full">
              <SelectValue placeholder="选择建议" />
            </SelectTrigger>
            <SelectContent>
              {proposals.map((proposal) => (
                <SelectItem
                  key={proposal.proposal_id}
                  value={proposal.proposal_id}
                >
                  {proposal.proposal_id} / {proposal.proposed_disposition}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="outcome-disposition">
            实际处置
          </label>
          <Select
            value={observedDisposition}
            onValueChange={(value) =>
              setObservedDisposition(value as SocOperationalDisposition)
            }
          >
            <SelectTrigger id="outcome-disposition" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DISPOSITION_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="outcome-review-kind">
            标签来源
          </label>
          <Select
            value={reviewKind}
            onValueChange={(value) =>
              setReviewKind(value as SocDispositionOutcomeReviewKind)
            }
          >
            <SelectTrigger id="outcome-review-kind" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OUTCOME_REVIEW_KIND_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {sampled ? (
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor="outcome-sample-id">
              抽样批次
            </label>
            <Input
              id="outcome-sample-id"
              value={sampleId}
              onChange={(event) => setSampleId(event.target.value)}
              placeholder="DSAMPLE-..."
            />
          </div>
        ) : (
          <div className="flex items-end">
            <div className="text-muted-foreground text-sm">
              {latestOutcome
                ? `修订 ${latestOutcome.outcome_id}`
                : "首次记录该复核通道"}
            </div>
          </div>
        )}

        <div className="space-y-2 lg:col-span-2">
          <label className="text-sm font-medium" htmlFor="outcome-reason">
            标签理由
          </label>
          <Textarea
            id="outcome-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="min-h-24 resize-none"
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 lg:col-span-2">
          <span className="text-muted-foreground text-xs">
            {queueStatus === "closed"
              ? latestOutcome
                ? `将显式 supersede ${latestOutcome.outcome_id}`
                : "工单已关闭，可记录标签"
              : "工单关闭后可记录标签"}
          </span>
          <Button
            size="sm"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit || mutation.isPending}
          >
            <ClipboardCheckIcon className="size-4" />
            {latestOutcome ? "提交修订" : "记录标签"}
          </Button>
        </div>
      </div>
    </section>
  );
}

function DispositionOutcomeSection({
  outcomes,
}: {
  outcomes: SocDispositionOutcomeRecord[];
}) {
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <FlaskConicalIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">影子评测结果</h3>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{outcomes.length}</Badge>
          <Badge variant="outline">无决策影响</Badge>
        </div>
      </div>
      <div className="divide-y">
        {outcomes.length === 0 ? (
          <div className="text-muted-foreground p-4 text-sm">
            当前建议还没有结构化的分析师或抽样复核标签。
          </div>
        ) : (
          outcomes.map((outcome) => (
            <div key={outcome.outcome_id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {outcome.outcome_status}
                  </div>
                  <div className="text-muted-foreground mt-1 text-xs">
                    {outcome.outcome_id} / {formatTime(outcome.observed_at)}
                  </div>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <Badge variant="outline">{outcome.review_kind}</Badge>
                  <Badge variant="secondary">shadow evaluation</Badge>
                </div>
              </div>
              <p className="text-muted-foreground mt-3 text-xs">
                {outcome.reason}
              </p>
              <div className="text-muted-foreground mt-3 grid gap-1 text-xs sm:grid-cols-2">
                <span>proposed {outcome.proposed_disposition}</span>
                <span>observed {outcome.observed_disposition}</span>
                <span>source {outcome.source}</span>
                <span>reviewer {outcome.reviewed_by.actor_id}</span>
                <span>sample {outcome.sample_id ?? "routine"}</span>
                <span>queue impact {outcome.review_queue_impact}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function ExternalDispositionSection({
  records,
}: {
  records: SocExternalDispositionRecord[];
}) {
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <InboxIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">外部处置反馈</h3>
        </div>
        <Badge variant="secondary">{records.length}</Badge>
      </div>
      <div className="divide-y">
        {records.length === 0 ? (
          <div className="text-muted-foreground p-4 text-sm">
            当前工单还没有外部系统同步的处置状态或理由。
          </div>
        ) : (
          records.map((record) => (
            <div key={record.disposition_id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {record.event.external_system} /{" "}
                    {record.event.external_case_id}
                  </div>
                  <div className="text-muted-foreground mt-1 text-xs">
                    外部更新 {formatTime(record.event.updated_at)} / 本地记录{" "}
                    {formatTime(record.created_at)}
                  </div>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <Badge variant="outline">
                    {record.event.external_status}
                  </Badge>
                  <Badge variant="secondary">{record.canonical_status}</Badge>
                  <Badge
                    variant={
                      record.apply_status === "mapped" ? "default" : "outline"
                    }
                  >
                    {record.apply_status}
                  </Badge>
                </div>
              </div>
              <p className="text-muted-foreground mt-2 text-xs">
                {record.event.external_reason ?? record.apply_reason}
              </p>
              <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                {record.matched_by ? (
                  <span>matched: {record.matched_by}</span>
                ) : null}
                {record.correction_id ? (
                  <span>correction: {record.correction_id}</span>
                ) : null}
                {record.memory_candidate_id ? (
                  <span>memory candidate: {record.memory_candidate_id}</span>
                ) : null}
                {record.audit_id ? <span>audit: {record.audit_id}</span> : null}
              </div>
              {record.event.external_tags.length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {record.event.external_tags.map((tag) => (
                    <Badge key={tag} variant="outline">
                      {tag}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function MemoryCandidateSection({
  candidates,
  reviewDrafts,
  isReviewing,
  isDraftingLesson,
  onReviewDraftChange,
  onReview,
  onDraftLesson,
}: {
  candidates: SocMemoryCandidate[];
  reviewDrafts: Record<string, MemoryCandidateReviewDraft>;
  isReviewing: boolean;
  isDraftingLesson: boolean;
  onReviewDraftChange: (
    candidate: SocMemoryCandidate,
    patch: Partial<MemoryCandidateReviewDraft>,
  ) => void;
  onReview: (
    candidate: SocMemoryCandidate,
    decision: SocMemoryCandidateReviewDecision,
  ) => void;
  onDraftLesson: (candidate: SocMemoryCandidate) => void;
}) {
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <CircleIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">候选记忆</h3>
        </div>
        <Badge variant="secondary">{candidates.length}</Badge>
      </div>
      <div className="divide-y">
        {candidates.length === 0 ? (
          <div className="text-muted-foreground p-4 text-sm">
            当前工单还没有待评审经验。
          </div>
        ) : (
          candidates.map((candidate) => {
            const draft =
              reviewDrafts[candidate.candidate_id] ??
              defaultMemoryCandidateReviewDraft(candidate);
            const applicability = candidate.applicability;
            const decisionEligible =
              candidate.decision_impact === "detection_decision" &&
              applicability !== null &&
              applicability !== undefined;
            const narrowedApplicability = reviewedMemoryApplicability(
              candidate,
              draft,
            );
            const effectiveApplicability =
              narrowedApplicability ?? applicability;
            const reviewedLesson = reviewedMemoryBusinessLesson(
              draft,
              effectiveApplicability,
            );
            const editable = ["pending_review", "confirmed_candidate"].includes(
              candidate.status,
            );
            const cohortMetrics = candidateCohortMetrics(candidate);
            const scopeHighlights = candidateScopeHighlights(candidate);
            const hasLessonDraft = hasMemoryLessonDraft(draft);
            const reviewContextId = `memory-review-context-${candidate.candidate_id}`;
            return (
              <div key={candidate.candidate_id} className="p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0 sm:flex-1">
                    <div className="text-sm font-semibold break-words">
                      {candidate.summary}
                    </div>
                    <div className="text-muted-foreground mt-1 text-xs">
                      Candidate {candidate.candidate_id} · 创建于{" "}
                      {formatTime(candidate.created_at)}
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-start gap-2 sm:justify-end">
                    <Badge variant="outline">
                      {candidateStatusLabel(candidate.status)}
                    </Badge>
                    <Badge variant="secondary">
                      {decisionEligible ? "可形成决策经验" : "仅作分析参考"}
                    </Badge>
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-2 overflow-hidden border lg:grid-cols-4">
                  <CandidateMetric
                    label="候选结论"
                    value={candidateRiskClassLabel(cohortMetrics.riskClass)}
                  />
                  <CandidateMetric
                    label="有效告警"
                    value={
                      cohortMetrics.supportCount === null
                        ? "-"
                        : `${cohortMetrics.supportCount} 条`
                    }
                  />
                  <CandidateMetric
                    label="独立来源"
                    value={
                      cohortMetrics.distinctSourceCount === null
                        ? "-"
                        : `${cohortMetrics.distinctSourceCount} 条`
                    }
                  />
                  <CandidateMetric
                    label="结论一致率"
                    value={formatPercent(cohortMetrics.consistencyRatio)}
                  />
                </div>

                {scopeHighlights.length > 0 ? (
                  <div className="mt-4">
                    <div className="text-xs font-medium">核心匹配条件</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {scopeHighlights.map(({ label, values }) => (
                        <Badge
                          key={label}
                          variant="outline"
                          className="max-w-full text-left break-all whitespace-normal"
                        >
                          {label}：{values.join(", ")}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}

                {applicability ? (
                  <Collapsible className="mt-4 border-t">
                    <CollapsibleTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        className="group h-auto w-full justify-between rounded-none px-0 py-3 text-xs"
                      >
                        <span>
                          高级匹配范围 · {applicability.profile_id}@
                          {applicability.profile_version}
                        </span>
                        <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="pb-3">
                      <p className="text-muted-foreground text-xs">
                        必需条件全部命中才可能复用；勾选可选条件会进一步收窄范围。
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {Object.entries(applicability.required_facets).map(
                          ([key, values]) => (
                            <Badge key={key} variant="outline">
                              必需 {key}: {values.slice(0, 3).join(", ")}
                            </Badge>
                          ),
                        )}
                      </div>
                      {Object.keys(applicability.optional_facets).length > 0 ? (
                        <div className="mt-3 grid gap-2 md:grid-cols-2">
                          {Object.entries(applicability.optional_facets).map(
                            ([key, values]) => {
                              const checked =
                                draft.promotedFacetKeys.includes(key);
                              const inputId = `memory-scope-${candidate.candidate_id}-${key}`;
                              return (
                                <label
                                  key={key}
                                  htmlFor={inputId}
                                  className="flex min-w-0 items-start gap-2 text-xs"
                                >
                                  <input
                                    id={inputId}
                                    type="checkbox"
                                    checked={checked}
                                    disabled={isReviewing || !editable}
                                    className="mt-0.5 size-4 shrink-0"
                                    onChange={(event) =>
                                      onReviewDraftChange(candidate, {
                                        promotedFacetKeys: event.target.checked
                                          ? [
                                              ...draft.promotedFacetKeys.filter(
                                                (item) => item !== key,
                                              ),
                                              key,
                                            ]
                                          : draft.promotedFacetKeys.filter(
                                              (item) => item !== key,
                                            ),
                                      })
                                    }
                                  />
                                  <span className="min-w-0">
                                    <span className="font-medium">{key}</span>
                                    <span className="text-muted-foreground ml-1 break-all">
                                      {values.slice(0, 3).join(", ")}
                                    </span>
                                  </span>
                                </label>
                              );
                            },
                          )}
                        </div>
                      ) : null}
                    </CollapsibleContent>
                  </Collapsible>
                ) : null}

                <div className="mt-4 border-t pt-4">
                  <div className="text-sm font-semibold">1. 确认业务判断</div>
                  <div className="mt-3 grid gap-3 md:grid-cols-[14rem_minmax(0,1fr)]">
                    <label className="grid content-start gap-1 text-xs font-medium">
                      最终判断
                      <Select
                        value={draft.confirmedVerdict ?? undefined}
                        disabled={isReviewing || !editable}
                        onValueChange={(value) =>
                          onReviewDraftChange(candidate, {
                            confirmedVerdict: value as SocVerdict,
                            applyToFutureMatches: false,
                            lessonConclusion: "",
                            lessonBusinessRationale: "",
                            lessonGeneralizationBoundary: "",
                            lessonInvalidationCondition: "",
                            lessonHandlingGuidance: "",
                            lessonDraftProvenance: "",
                            lessonDraftUncertainties: [],
                            lessonEditing: false,
                          })
                        }
                      >
                        <SelectTrigger
                          className="h-9 w-full text-xs"
                          aria-label="最终业务判断"
                        >
                          <SelectValue placeholder="请选择" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="false_positive">误报</SelectItem>
                          <SelectItem value="true_positive">
                            真实攻击
                          </SelectItem>
                          <SelectItem value="suspicious">可疑</SelectItem>
                        </SelectContent>
                      </Select>
                    </label>
                    <label
                      htmlFor={reviewContextId}
                      className="grid gap-1 text-xs font-medium"
                    >
                      业务事实（可选）
                      <Textarea
                        id={reviewContextId}
                        value={draft.businessContext}
                        onChange={(event) =>
                          onReviewDraftChange(candidate, {
                            businessContext: event.target.value,
                          })
                        }
                        placeholder="例如：已确认这是 Windows Update/WinRE 更新流程中的正常行为。"
                        className="min-h-20 text-xs"
                        disabled={isReviewing || !editable}
                      />
                    </label>
                  </div>
                  <div className="mt-3 flex justify-end">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={
                        isReviewing ||
                        isDraftingLesson ||
                        !editable ||
                        draft.confirmedVerdict === null
                      }
                      onClick={() => onDraftLesson(candidate)}
                    >
                      <SparklesIcon className="size-4" />
                      {isDraftingLesson
                        ? "生成中"
                        : hasLessonDraft
                          ? "重新生成 Memory"
                          : "AI 生成 Memory"}
                    </Button>
                  </div>
                </div>

                {hasLessonDraft ? (
                  <div className="mt-4 border-t pt-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold">
                          2. 审阅 Business Lesson
                        </div>
                        <div className="text-muted-foreground mt-1 text-xs">
                          {draft.lessonDraftProvenance}
                        </div>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={isReviewing || !editable}
                        onClick={() =>
                          onReviewDraftChange(candidate, {
                            lessonEditing: !draft.lessonEditing,
                          })
                        }
                      >
                        <PencilIcon className="size-4" />
                        {draft.lessonEditing ? "完成编辑" : "编辑"}
                      </Button>
                    </div>
                    {draft.lessonDraftUncertainties.length > 0 ? (
                      <div className="mt-3 border-y border-amber-300 bg-amber-50 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
                        {draft.lessonDraftUncertainties.join("；")}
                      </div>
                    ) : null}
                    {draft.lessonEditing || !reviewedLesson ? (
                      <div className="mt-4 grid gap-3 md:grid-cols-2">
                        <label className="grid gap-1 text-xs font-medium md:col-span-2">
                          经验结论
                          <Textarea
                            value={draft.lessonConclusion}
                            disabled={isReviewing || !editable}
                            className="min-h-16 text-xs"
                            onChange={(event) =>
                              onReviewDraftChange(candidate, {
                                lessonConclusion: event.target.value,
                              })
                            }
                          />
                        </label>
                        <label className="grid gap-1 text-xs font-medium md:col-span-2">
                          业务依据（每行一条）
                          <Textarea
                            value={draft.lessonBusinessRationale}
                            disabled={isReviewing || !editable}
                            className="min-h-20 text-xs"
                            onChange={(event) =>
                              onReviewDraftChange(candidate, {
                                lessonBusinessRationale: event.target.value,
                              })
                            }
                          />
                        </label>
                        <div className="grid gap-2 border-y py-3 text-xs md:col-span-2">
                          <div className="font-medium">
                            适用条件（系统生成）
                          </div>
                          <div className="flex flex-wrap gap-2">
                            {Object.entries(
                              effectiveApplicability?.required_facets ?? {},
                            ).map(([key, values]) => (
                              <Badge
                                key={key}
                                variant="outline"
                                className="max-w-full break-all whitespace-normal"
                              >
                                {key}：{values.slice(0, 3).join(", ")}
                              </Badge>
                            ))}
                          </div>
                        </div>
                        <label className="grid gap-1 text-xs font-medium">
                          泛化边界（每行一条）
                          <Textarea
                            value={draft.lessonGeneralizationBoundary}
                            disabled={isReviewing || !editable}
                            className="min-h-20 text-xs"
                            onChange={(event) =>
                              onReviewDraftChange(candidate, {
                                lessonGeneralizationBoundary:
                                  event.target.value,
                              })
                            }
                          />
                        </label>
                        <label className="grid gap-1 text-xs font-medium">
                          失效条件（每行一条）
                          <Textarea
                            value={draft.lessonInvalidationCondition}
                            disabled={isReviewing || !editable}
                            className="min-h-20 text-xs"
                            onChange={(event) =>
                              onReviewDraftChange(candidate, {
                                lessonInvalidationCondition: event.target.value,
                              })
                            }
                          />
                        </label>
                        <label className="grid gap-1 text-xs font-medium md:col-span-2">
                          处置建议（每行一条）
                          <Textarea
                            value={draft.lessonHandlingGuidance}
                            disabled={isReviewing || !editable}
                            className="min-h-20 text-xs"
                            onChange={(event) =>
                              onReviewDraftChange(candidate, {
                                lessonHandlingGuidance: event.target.value,
                              })
                            }
                          />
                        </label>
                      </div>
                    ) : (
                      <div className="mt-4">
                        <MemoryLessonReadView lesson={reviewedLesson} />
                      </div>
                    )}
                  </div>
                ) : null}

                {decisionEligible && hasLessonDraft ? (
                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
                    <div>
                      <div className="text-sm font-semibold">
                        3. 设置未来复用
                      </div>
                      <p className="text-muted-foreground mt-1 text-xs">
                        精确命中机器适用范围后，复用本次审核结论。
                      </p>
                    </div>
                    <label className="flex items-center gap-2 text-xs font-medium">
                      <Switch
                        checked={draft.applyToFutureMatches}
                        disabled={
                          isReviewing ||
                          !editable ||
                          reviewedLesson === undefined
                        }
                        onCheckedChange={(checked) =>
                          onReviewDraftChange(candidate, {
                            applyToFutureMatches: checked,
                          })
                        }
                        aria-label="未来精确匹配改判"
                      />
                      用于未来精确匹配
                    </label>
                  </div>
                ) : null}

                <Collapsible className="mt-4 border-t">
                  <CollapsibleTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      className="group h-auto w-full justify-between rounded-none px-0 py-3 text-xs"
                    >
                      <span>
                        查看原始候选与审计材料 ·{" "}
                        {candidate.evidence_refs.length} 条引用
                      </span>
                      <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="space-y-3 pb-3">
                    <div className="text-muted-foreground max-h-72 overflow-y-auto border p-3 text-xs whitespace-pre-wrap">
                      {candidate.content}
                    </div>
                    <div className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-1 text-xs">
                      <span>{candidateSourceLabel(candidate)}</span>
                      <span>{candidate.candidate_type}</span>
                      <span>{candidate.tenant_scope}</span>
                      <span>runtime: inactive</span>
                      {candidate.review_owner ? (
                        <span>owner: {candidate.review_owner}</span>
                      ) : null}
                    </div>
                    {candidate.labels.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {candidate.labels.map((label) => (
                          <Badge key={label} variant="secondary">
                            {label}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                    {candidate.evidence_refs.length > 0 ? (
                      <div className="text-muted-foreground max-h-40 overflow-y-auto border p-3 font-mono text-xs break-all">
                        {candidate.evidence_refs.join("\n")}
                      </div>
                    ) : null}
                  </CollapsibleContent>
                </Collapsible>

                <div className="mt-4 flex flex-wrap justify-end gap-2 border-t pt-4">
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      disabled={
                        isReviewing ||
                        !["pending_review", "confirmed_candidate"].includes(
                          candidate.status,
                        ) ||
                        draft.confirmedVerdict === null ||
                        !reviewedLesson
                      }
                      onClick={() => onReview(candidate, "confirm")}
                    >
                      确认候选
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={
                        isReviewing ||
                        !["pending_review", "confirmed_candidate"].includes(
                          candidate.status,
                        )
                      }
                      onClick={() => onReview(candidate, "reject")}
                    >
                      驳回
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={
                        isReviewing ||
                        ![
                          "pending_review",
                          "confirmed_candidate",
                          "confirmed",
                        ].includes(candidate.status)
                      }
                      onClick={() =>
                        onReview(
                          candidate,
                          candidate.status === "confirmed"
                            ? "deprecate"
                            : "expire",
                        )
                      }
                    >
                      {candidate.status === "confirmed" ? "废弃" : "过期"}
                    </Button>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function RelevantMemorySection({
  result,
}: {
  result: SocMemoryRetrievalResult | null | undefined;
}) {
  const matches = result?.matches ?? [];
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <SearchCheckIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">相关确认记忆</h3>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{matches.length}</Badge>
          {result ? (
            <Badge variant="outline">
              {result.total_token_estimate}/{result.max_tokens} tokens
            </Badge>
          ) : null}
        </div>
      </div>
      {!result ? (
        <div className="text-muted-foreground p-4 text-sm">
          当前上下文没有 memory retrieval result。
        </div>
      ) : (
        <div className="divide-y">
          <div className="text-muted-foreground grid gap-2 p-4 text-xs sm:grid-cols-4">
            <span>候选 {result.total_candidate_count}</span>
            <span>未开启检索 {result.skipped_retrieval_disabled}</span>
            <span>未治理启用 {result.skipped_ungoverned_activation}</span>
            <span>启用已过期 {result.skipped_activation_expired}</span>
            <span>逾期未复核 {result.skipped_review_overdue}</span>
            <span>状态过滤 {result.skipped_status}</span>
            <span>强锚点过滤 {result.skipped_missing_strong_anchor}</span>
            <span>适用范围过滤 {result.skipped_not_applicable}</span>
            <span>低分过滤 {result.skipped_below_min_score}</span>
            <span>仅上下文 {result.returned_context_only_count}</span>
          </div>
          {matches.length === 0 ? (
            <div className="text-muted-foreground p-4 text-sm">
              没有命中 retrieval-enabled 的 confirmed memory。
            </div>
          ) : (
            matches.map((match) => (
              <div key={match.memory_id} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">
                      {match.record.summary}
                    </div>
                    <div className="text-muted-foreground mt-1 text-xs">
                      {match.memory_id} v{match.version} / score{" "}
                      {match.score.toFixed(2)} / {match.token_estimate} tokens
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    <Badge variant="outline">{match.record.memory_type}</Badge>
                    <Badge variant="secondary">
                      {match.record.target_artifact}
                    </Badge>
                    <Badge variant="outline">retrieval-enabled</Badge>
                    {match.applicability_report ? (
                      <Badge
                        variant={
                          match.applicability_report.context_only_allowed
                            ? "outline"
                            : "secondary"
                        }
                      >
                        {match.applicability_report.context_only_allowed
                          ? "context-only"
                          : match.applicability_report.status}
                      </Badge>
                    ) : null}
                  </div>
                </div>
                <p className="text-muted-foreground mt-2 text-xs">
                  {match.record.content}
                </p>
                {match.match_reasons.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {match.match_reasons.map((reason) => (
                      <Badge key={reason} variant="outline">
                        {reason}
                      </Badge>
                    ))}
                  </div>
                ) : null}
                <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                  <span>content {match.content_hash}</span>
                  <span>facets {match.facets_hash}</span>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}

function MemoryRetrievalActivationSection({
  records,
  drafts,
  isUpdating,
  onDraftChange,
  onAction,
}: {
  records: SocMemoryRecord[];
  drafts: Record<string, MemoryRetrievalDraft>;
  isUpdating: boolean;
  onDraftChange: (
    memoryId: string,
    field: keyof MemoryRetrievalDraft,
    value: string,
  ) => void;
  onAction: (
    record: SocMemoryRecord,
    action: SocMemoryRetrievalActivationAction,
  ) => void;
}) {
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-2">
          <LibraryBigIcon className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">确认记忆检索治理</h3>
        </div>
        <Badge variant="secondary">{records.length}</Badge>
      </div>
      <div className="divide-y">
        {records.length === 0 ? (
          <div className="text-muted-foreground p-4 text-sm">
            当前工单没有已确认的记忆记录。
          </div>
        ) : (
          records.map((record) => {
            const draft =
              drafts[record.memory_id] ?? defaultMemoryRetrievalDraft();
            const nextAction: SocMemoryRetrievalActivationAction =
              record.retrieval_enabled ? "disable" : "enable";
            const reviewAfterDays = Number(draft.reviewAfterDays);
            const invalidEnable =
              nextAction === "enable" &&
              (!draft.validUntil ||
                !Number.isInteger(reviewAfterDays) ||
                reviewAfterDays < 1);
            return (
              <div key={record.memory_id} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">
                      {record.summary}
                    </div>
                    <div className="text-muted-foreground mt-1 text-xs">
                      {record.memory_id} v{record.version} /{" "}
                      {record.memory_type}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">{record.status}</Badge>
                    <Badge
                      variant={
                        record.retrieval_enabled ? "secondary" : "outline"
                      }
                    >
                      {record.retrieval_enabled
                        ? "retrieval enabled"
                        : "retrieval disabled"}
                    </Badge>
                  </div>
                </div>
                <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem_8rem_auto] lg:items-end">
                  <label className="grid gap-1 text-xs">
                    <span className="text-muted-foreground">治理理由</span>
                    <Input
                      value={draft.reason}
                      onChange={(event) =>
                        onDraftChange(
                          record.memory_id,
                          "reason",
                          event.target.value,
                        )
                      }
                      disabled={isUpdating}
                    />
                  </label>
                  <label className="grid gap-1 text-xs">
                    <span className="text-muted-foreground">有效至</span>
                    <Input
                      type="datetime-local"
                      value={draft.validUntil}
                      onChange={(event) =>
                        onDraftChange(
                          record.memory_id,
                          "validUntil",
                          event.target.value,
                        )
                      }
                      disabled={isUpdating || nextAction === "disable"}
                    />
                  </label>
                  <label className="grid gap-1 text-xs">
                    <span className="text-muted-foreground">复核天数</span>
                    <Input
                      type="number"
                      min={1}
                      max={365}
                      value={draft.reviewAfterDays}
                      onChange={(event) =>
                        onDraftChange(
                          record.memory_id,
                          "reviewAfterDays",
                          event.target.value,
                        )
                      }
                      disabled={isUpdating || nextAction === "disable"}
                    />
                  </label>
                  <Button
                    size="sm"
                    variant={nextAction === "enable" ? "default" : "outline"}
                    disabled={
                      isUpdating || !draft.reason.trim() || invalidEnable
                    }
                    onClick={() => onAction(record, nextAction)}
                  >
                    <PowerIcon className="size-4" />
                    {nextAction === "enable" ? "启用检索" : "停用检索"}
                  </Button>
                </div>
                {record.retrieval_updated_at ? (
                  <div className="text-muted-foreground mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                    <span>{record.retrieval_policy_version}</span>
                    <span>{formatTime(record.retrieval_updated_at)}</span>
                    {record.retrieval_review_due_at ? (
                      <span>
                        review {formatTime(record.retrieval_review_due_at)}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function parseJsonObject<T>(value: string): T {
  const parsed: unknown = JSON.parse(value);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON 必须是对象");
  }
  return parsed as T;
}

function QueueItemButton({
  item,
  active,
  onClick,
}: {
  item: SocReviewQueueItem;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded-md border p-3 text-left transition-colors",
        "hover:bg-accent focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] focus-visible:outline-none",
        active ? "border-primary bg-accent" : "border-border bg-background",
      )}
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">
            {queueItemLabel(item)}
          </div>
          <div className="text-muted-foreground mt-1 truncate text-xs">
            {item.alert_id} / {item.source_type}
          </div>
        </div>
        <Badge className={priorityClass(item.priority)} variant="outline">
          {item.priority}
        </Badge>
      </div>
      <div className="text-muted-foreground mt-3 line-clamp-2 text-xs">
        {item.reason}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 text-xs">
        <span className="text-muted-foreground">
          {formatTime(item.updated_at)}
        </span>
        <span className="text-muted-foreground">
          {verdictLabel(item.verdict)}
        </span>
      </div>
    </button>
  );
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 border-b py-2 text-sm last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

function EmptyDetail() {
  return (
    <div className="flex min-h-[28rem] flex-col items-center justify-center gap-3 border-l text-center">
      <div className="bg-muted flex size-12 items-center justify-center rounded-full">
        <ShieldAlertIcon className="text-muted-foreground size-6" />
      </div>
      <div>
        <p className="text-sm font-medium">选择一条复核项</p>
        <p className="text-muted-foreground mt-1 text-sm">
          查看研判上下文、相似告警和人工纠正入口。
        </p>
      </div>
    </div>
  );
}

export function SocReviewQueueWorkbench({
  initialQueueId,
  initialCandidateId,
}: {
  initialQueueId?: string;
  initialCandidateId?: string;
}) {
  const [workspaceView, setWorkspaceView] = useState<
    "queue" | "memory" | "sample"
  >(initialCandidateId ? "memory" : "queue");
  const [statusFilter, setStatusFilter] = useState<
    SocReviewQueueStatus | "all"
  >(initialQueueId ? "all" : "open");
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(
    initialQueueId ?? null,
  );
  const [sampleReviewTarget, setSampleReviewTarget] =
    useState<SocDispositionSampleReviewTarget | null>(null);
  const [closeReason, setCloseReason] = useState("复核完成");
  const [correctionReason, setCorrectionReason] = useState("");
  const [correctedVerdict, setCorrectedVerdict] =
    useState<SocVerdict>("false_positive");
  const [selectedApprovalRequestId, setSelectedApprovalRequestId] = useState<
    string | null
  >(null);
  const [approvalRequestJson, setApprovalRequestJson] = useState(
    DEFAULT_APPROVAL_REQUEST_JSON,
  );
  const [approvalReason, setApprovalReason] = useState("批准手工验证执行边界");
  const [approvalExpirySeconds, setApprovalExpirySeconds] = useState("900");
  const [approvedActionPayloadJson, setApprovedActionPayloadJson] =
    useState("{}");
  const [approvalGrant, setApprovalGrant] =
    useState<SocAgentApprovalGrant | null>(null);
  const [approvedActionResult, setApprovedActionResult] =
    useState<SocAgentActionResult | null>(null);
  const [memoryReviewDrafts, setMemoryReviewDrafts] = useState<
    Record<string, MemoryCandidateReviewDraft>
  >({});
  const [memoryRetrievalDrafts, setMemoryRetrievalDrafts] = useState<
    Record<string, MemoryRetrievalDraft>
  >({});

  const status = statusFilter === "all" ? null : statusFilter;
  const { items, isLoading, isFetching, error, refetch } = useSocReviewItems({
    status,
    limit: 50,
  });
  const selectedItem = useMemo(
    () => items.find((item) => item.queue_id === selectedQueueId) ?? null,
    [items, selectedQueueId],
  );
  const selectedSampleItem =
    sampleReviewTarget?.queueItem.queue_id === selectedQueueId
      ? sampleReviewTarget.queueItem
      : null;
  const fallbackSelectedItem =
    selectedItem ?? selectedSampleItem ?? items[0] ?? null;
  const activeQueueId =
    selectedItem?.queue_id ?? fallbackSelectedItem?.queue_id;
  const activeSampleReviewTarget =
    sampleReviewTarget &&
    sampleReviewTarget.queueItem.queue_id === activeQueueId &&
    sampleReviewTarget.canRecordOutcome
      ? sampleReviewTarget
      : null;
  const { context, isLoading: contextLoading } =
    useSocReviewContext(activeQueueId);
  const {
    candidate: focusedMemoryCandidate,
    isLoading: focusedMemoryCandidateLoading,
    error: focusedMemoryCandidateError,
  } = useSocMemoryCandidate(initialCandidateId);
  const {
    candidates: pendingMemoryCandidates,
    isLoading: pendingMemoryCandidatesLoading,
    error: pendingMemoryCandidatesError,
  } = useSocMemoryCandidates({
    status: "pending_review",
    limit: 50,
    enabled: workspaceView === "memory" && !initialCandidateId,
  });
  const standaloneMemoryCandidates = useMemo(
    () =>
      focusedMemoryCandidate
        ? [focusedMemoryCandidate]
        : pendingMemoryCandidates,
    [focusedMemoryCandidate, pendingMemoryCandidates],
  );
  const activeMemoryCandidates = useMemo(
    () =>
      workspaceView === "memory"
        ? standaloneMemoryCandidates
        : (context?.memory_candidates ?? []),
    [context?.memory_candidates, standaloneMemoryCandidates, workspaceView],
  );
  const { records: confirmedMemoryRecords } = useSocMemoryRecords({
    status: "confirmed",
    limit: 200,
  });
  const relatedMemoryRecords = useMemo(() => {
    const candidateIds = new Set(
      activeMemoryCandidates.map((candidate) => candidate.candidate_id),
    );
    return confirmedMemoryRecords.filter((record) =>
      candidateIds.has(record.source_candidate_id),
    );
  }, [activeMemoryCandidates, confirmedMemoryRecords]);
  const {
    requests: approvalRequests,
    isLoading: approvalRequestsLoading,
    isFetching: approvalRequestsFetching,
    error: approvalRequestsError,
    refetch: refetchApprovalRequests,
  } = useSocApprovalRequests({ status: "pending", limit: 50 });
  const fallbackSelectedApprovalRequest = useMemo(
    () =>
      approvalRequests.find(
        (request) => request.approval_request_id === selectedApprovalRequestId,
      ) ??
      approvalRequests[0] ??
      null,
    [approvalRequests, selectedApprovalRequestId],
  );
  const activeApprovalRequestId =
    selectedApprovalRequestId ??
    fallbackSelectedApprovalRequest?.approval_request_id;
  const {
    request: selectedApprovalRequest,
    isLoading: approvalRequestLoading,
  } = useSocApprovalRequest(activeApprovalRequestId);
  const activeApprovalRequest =
    selectedApprovalRequest ?? fallbackSelectedApprovalRequest;
  const closeMutation = useCloseSocReviewItem();
  const correctMutation = useCorrectSocReviewRun();
  const createApprovalGrantMutation = useCreateSocApprovalGrant();
  const rejectApprovalRequestMutation = useRejectSocApprovalRequest();
  const expireApprovalRequestMutation = useExpireSocApprovalRequest();
  const dryRunApprovedActionMutation = useDryRunSocApprovedAction();
  const executeApprovedActionMutation = useExecuteSocApprovedAction();
  const reviewMemoryCandidateMutation = useReviewSocMemoryCandidate();
  const draftMemoryLessonMutation = useDraftSocMemoryBusinessLesson();
  const updateMemoryRetrievalMutation = useUpdateSocMemoryRetrievalActivation();

  const handleOpenSampleReview = (target: SocDispositionSampleReviewTarget) => {
    setSampleReviewTarget(target);
    setSelectedQueueId(target.queueItem.queue_id);
    setStatusFilter("all");
    setWorkspaceView("queue");
  };

  useEffect(() => {
    const firstRequestId = approvalRequests[0]?.approval_request_id;
    if (!selectedApprovalRequestId && firstRequestId) {
      setSelectedApprovalRequestId(firstRequestId);
    }
  }, [approvalRequests, selectedApprovalRequestId]);

  useEffect(() => {
    if (!activeApprovalRequest) return;
    setApprovalRequestJson(JSON.stringify(activeApprovalRequest, null, 2));
    setApprovalGrant(null);
    setApprovedActionResult(null);
  }, [activeApprovalRequest]);

  const handleClose = async () => {
    if (!activeQueueId || closeReason.trim().length === 0) return;
    try {
      await closeMutation.mutateAsync({
        queueId: activeQueueId,
        request: { reason: closeReason.trim() },
      });
      toast.success("复核项已关闭");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "关闭失败");
    }
  };

  const handleCorrect = async () => {
    const runId = context?.run.run_id ?? fallbackSelectedItem?.run_id;
    if (!runId || correctionReason.trim().length === 0) return;
    try {
      await correctMutation.mutateAsync({
        runId,
        request: {
          verdict: correctedVerdict,
          reason: correctionReason.trim(),
        },
      });
      toast.success("人工纠正已记录");
      setCorrectionReason("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "纠正失败");
    }
  };

  const handleCreateApprovalGrant = async () => {
    if (approvalReason.trim().length === 0) return;
    const expiresInSeconds = Number.parseInt(approvalExpirySeconds, 10);
    if (!Number.isFinite(expiresInSeconds) || expiresInSeconds <= 0) {
      toast.error("有效期必须是正整数秒");
      return;
    }

    try {
      if (!activeApprovalRequest?.approval_request_id) {
        toast.error("请选择待审批请求");
        return;
      }
      const grant = await createApprovalGrantMutation.mutateAsync({
        approval_request_id: activeApprovalRequest.approval_request_id,
        reason: approvalReason.trim(),
        expires_in_seconds: expiresInSeconds,
      });
      setApprovalGrant(grant);
      setApprovedActionResult(null);
      toast.success("审批 token 已生成");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "审批失败");
    }
  };

  const handleResolveApprovalRequest = async (
    resolution: "reject" | "expire",
  ) => {
    if (!activeApprovalRequest?.approval_request_id) {
      toast.error("请选择待审批请求");
      return;
    }
    if (approvalReason.trim().length === 0) {
      toast.error("请填写审批原因");
      return;
    }
    try {
      const command = {
        approvalRequestId: activeApprovalRequest.approval_request_id,
        request: { reason: approvalReason.trim() },
      };
      if (resolution === "reject") {
        await rejectApprovalRequestMutation.mutateAsync(command);
      } else {
        await expireApprovalRequestMutation.mutateAsync(command);
      }
      setApprovalGrant(null);
      setApprovedActionResult(null);
      toast.success(
        resolution === "reject" ? "审批请求已驳回" : "审批请求已过期",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "审批状态更新失败");
    }
  };

  const buildApprovedActionCommand = (
    dryRun: boolean,
  ): SocAgentApprovedActionCommand | null => {
    if (!approvalGrant) return null;
    try {
      return {
        execution_token_id: approvalGrant.execution_token_id,
        route: approvalGrant.route,
        action: approvalGrant.action,
        dry_run: dryRun,
        payload: parseJsonObject<Record<string, unknown>>(
          approvedActionPayloadJson,
        ),
      };
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Payload 不是合法 JSON");
      return null;
    }
  };

  const handleDryRunApprovedAction = async () => {
    const command = buildApprovedActionCommand(true);
    if (!command) return;
    try {
      const result = await dryRunApprovedActionMutation.mutateAsync(command);
      setApprovedActionResult(result);
      toast.success("Dry-run 已通过");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Dry-run 失败");
    }
  };

  const handleExecuteApprovedAction = async () => {
    const command = buildApprovedActionCommand(false);
    if (!command) return;
    try {
      const result = await executeApprovedActionMutation.mutateAsync(command);
      setApprovedActionResult(result);
      setApprovalGrant((current) =>
        current
          ? {
              ...current,
              status: "consumed",
            }
          : current,
      );
      toast.success("执行边界已消费 token");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "执行失败");
    }
  };

  const handleMemoryReviewDraftChange = (
    candidate: SocMemoryCandidate,
    patch: Partial<MemoryCandidateReviewDraft>,
  ) => {
    setMemoryReviewDrafts((current) => ({
      ...current,
      [candidate.candidate_id]: {
        ...(current[candidate.candidate_id] ??
          defaultMemoryCandidateReviewDraft(candidate)),
        ...patch,
      },
    }));
  };

  const handleReviewMemoryCandidate = async (
    candidate: SocMemoryCandidate,
    decision: SocMemoryCandidateReviewDecision,
  ) => {
    const draft =
      memoryReviewDrafts[candidate.candidate_id] ??
      defaultMemoryCandidateReviewDraft(candidate);
    const reviewerVerdict = draft.confirmedVerdict;
    const reason =
      draft.businessContext.trim() ||
      (decision === "confirm" && reviewerVerdict
        ? `审核人确认 Business Lesson，最终判断为${verdictLabel(reviewerVerdict)}。`
        : decision === "reject"
          ? "审核人驳回该候选，未形成可复用 Memory。"
          : `审核人执行候选状态变更：${decision}。`);
    const narrowedApplicability = reviewedMemoryApplicability(candidate, draft);
    const effectiveApplicability =
      narrowedApplicability ?? candidate.applicability;
    const recordLesson = reviewedMemoryBusinessLesson(
      draft,
      effectiveApplicability,
    );
    if (decision === "confirm" && (!reviewerVerdict || !recordLesson)) {
      toast.error("请先选择最终判断并生成完整的 Business Lesson");
      return;
    }
    try {
      await reviewMemoryCandidateMutation.mutateAsync({
        candidateId: candidate.candidate_id,
        request: {
          decision,
          reason,
          ...(decision === "confirm" && narrowedApplicability
            ? { record_applicability: narrowedApplicability }
            : {}),
          ...(decision === "confirm" && recordLesson
            ? { record_lesson: recordLesson }
            : {}),
          ...(decision === "confirm" && draft.applyToFutureMatches
            ? {
                apply_to_future_matches: true,
                confirmed_verdict: reviewerVerdict,
                clear_review_on_match: true,
              }
            : {}),
        },
      });
      setMemoryReviewDrafts((current) => {
        const next = { ...current };
        delete next[candidate.candidate_id];
        return next;
      });
      toast.success("候选记忆已更新");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "候选记忆评审失败");
    }
  };

  const handleDraftMemoryBusinessLesson = async (
    candidate: SocMemoryCandidate,
  ) => {
    const current =
      memoryReviewDrafts[candidate.candidate_id] ??
      defaultMemoryCandidateReviewDraft(candidate);
    if (current.confirmedVerdict === null) {
      toast.error("请先选择最终业务判断");
      return;
    }
    try {
      const result = await draftMemoryLessonMutation.mutateAsync({
        candidateId: candidate.candidate_id,
        request: {
          reviewer_verdict: current.confirmedVerdict,
          reviewer_context: current.businessContext.trim() || null,
          promoted_facet_keys: current.promotedFacetKeys,
        },
      });
      if (result.reviewer_verdict !== current.confirmedVerdict) {
        throw new Error("AI 草稿与当前审核结论不一致，请重新生成");
      }
      const lesson = result.lesson;
      handleMemoryReviewDraftChange(candidate, {
        lessonConclusion: lesson.conclusion,
        lessonBusinessRationale: lesson.business_rationale.join("\n"),
        lessonGeneralizationBoundary:
          lesson.generalization_boundaries.join("\n"),
        lessonInvalidationCondition: lesson.invalidation_conditions.join("\n"),
        lessonHandlingGuidance: lesson.handling_guidance.join("\n"),
        lessonDraftProvenance: `${result.provenance.model_name} / ${result.provenance.prompt_version} / calls ${result.provenance.provider_call_count}${result.provenance.output_repair_call_count ? ` / repairs ${result.provenance.output_repair_call_count}` : ""}`,
        lessonDraftUncertainties: result.uncertainties,
        lessonEditing: false,
      });
      toast.success("AI 经验草稿已生成，请审核后确认");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "AI 经验草稿生成失败");
    }
  };

  const handleMemoryRetrievalDraftChange = (
    memoryId: string,
    field: keyof MemoryRetrievalDraft,
    value: string,
  ) => {
    setMemoryRetrievalDrafts((current) => ({
      ...current,
      [memoryId]: {
        ...(current[memoryId] ?? defaultMemoryRetrievalDraft()),
        [field]: value,
      },
    }));
  };

  const handleMemoryRetrievalAction = async (
    record: SocMemoryRecord,
    action: SocMemoryRetrievalActivationAction,
  ) => {
    const draft =
      memoryRetrievalDrafts[record.memory_id] ?? defaultMemoryRetrievalDraft();
    const reason = draft.reason.trim();
    if (!reason) {
      toast.error("请填写治理理由");
      return;
    }
    try {
      const enabling = action === "enable";
      await updateMemoryRetrievalMutation.mutateAsync({
        memoryId: record.memory_id,
        request: {
          action,
          expected_record_version: record.version,
          reason,
          ...(enabling
            ? {
                activation_valid_until: new Date(
                  draft.validUntil,
                ).toISOString(),
                review_after_days: Number(draft.reviewAfterDays),
              }
            : {}),
        },
      });
      setMemoryRetrievalDrafts((current) => ({
        ...current,
        [record.memory_id]: defaultMemoryRetrievalDraft(),
      }));
      toast.success(enabling ? "确认记忆检索已启用" : "确认记忆检索已停用");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "记忆检索状态更新失败");
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">SOC 复核</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            复核告警研判、候选经验与抽样结果，沉淀可审计的人工结论。
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 lg:w-auto lg:justify-end">
          <Button variant="outline" size="sm" asChild>
            <Link href="/workspace/soc/memory-validation">
              <FlaskConicalIcon className="size-4" />
              Memory 验证
            </Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link href="/workspace/soc/operations">
              <ActivityIcon className="size-4" />
              运营观察
            </Link>
          </Button>
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={workspaceView}
            onValueChange={(value) =>
              value && setWorkspaceView(value as "queue" | "memory" | "sample")
            }
          >
            <ToggleGroupItem value="queue" aria-label="告警复核队列">
              <InboxIcon className="size-4" />
              告警队列
            </ToggleGroupItem>
            <ToggleGroupItem value="memory" aria-label="候选经验审核">
              <LibraryBigIcon className="size-4" />
              候选经验
            </ToggleGroupItem>
            <ToggleGroupItem value="sample" aria-label="抽样复核批次">
              <FlaskConicalIcon className="size-4" />
              抽样复核
            </ToggleGroupItem>
          </ToggleGroup>
          <Button variant="outline" size="sm" asChild>
            <Link href="/workspace/soc/normalization">
              <WrenchIcon className="size-4" />
              归一化运维
            </Link>
          </Button>
          {workspaceView === "queue" ? (
            <>
              <Select
                value={statusFilter}
                onValueChange={(value) =>
                  setStatusFilter(value as SocReviewQueueStatus | "all")
                }
              >
                <SelectTrigger size="sm" className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void refetch()}
                disabled={isFetching}
              >
                <RefreshCwIcon
                  className={cn("size-4", isFetching && "animate-spin")}
                />
                刷新
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {workspaceView === "sample" ? (
        <div className="min-h-0 flex-1">
          <SocDispositionSampleInbox onOpenReview={handleOpenSampleReview} />
        </div>
      ) : null}
      {workspaceView === "memory" ? (
        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
            <section className="border px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold">
                    Memory Candidate 治理
                  </h2>
                  <p className="text-muted-foreground mt-1 text-sm">
                    审核跨告警模式经验，不会为无需人工复核的单条告警伪造
                    ReviewQueue 工单。
                  </p>
                </div>
                {initialCandidateId ? (
                  <Badge variant="outline">{initialCandidateId}</Badge>
                ) : (
                  <Badge variant="secondary">
                    {standaloneMemoryCandidates.length} pending
                  </Badge>
                )}
              </div>
            </section>

            {(
              initialCandidateId
                ? focusedMemoryCandidateLoading
                : pendingMemoryCandidatesLoading
            ) ? (
              <div className="text-muted-foreground flex min-h-48 items-center justify-center border text-sm">
                正在加载候选经验...
              </div>
            ) : (
                initialCandidateId
                  ? focusedMemoryCandidateError
                  : pendingMemoryCandidatesError
              ) ? (
              <div className="border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                {(initialCandidateId
                  ? focusedMemoryCandidateError
                  : pendingMemoryCandidatesError) instanceof Error
                  ? (initialCandidateId
                      ? focusedMemoryCandidateError
                      : pendingMemoryCandidatesError
                    )?.message
                  : "候选经验加载失败"}
              </div>
            ) : standaloneMemoryCandidates.length === 0 ? (
              <div className="text-muted-foreground flex min-h-48 items-center justify-center border text-sm">
                当前没有待审核的候选经验。
              </div>
            ) : (
              <>
                <MemoryCandidateSection
                  candidates={standaloneMemoryCandidates}
                  reviewDrafts={memoryReviewDrafts}
                  isReviewing={reviewMemoryCandidateMutation.isPending}
                  isDraftingLesson={draftMemoryLessonMutation.isPending}
                  onReviewDraftChange={handleMemoryReviewDraftChange}
                  onReview={(candidate, decision) =>
                    void handleReviewMemoryCandidate(candidate, decision)
                  }
                  onDraftLesson={(candidate) =>
                    void handleDraftMemoryBusinessLesson(candidate)
                  }
                />

                <MemoryRetrievalActivationSection
                  records={relatedMemoryRecords}
                  drafts={memoryRetrievalDrafts}
                  isUpdating={updateMemoryRetrievalMutation.isPending}
                  onDraftChange={handleMemoryRetrievalDraftChange}
                  onAction={(record, action) =>
                    void handleMemoryRetrievalAction(record, action)
                  }
                />
              </>
            )}
          </div>
        </main>
      ) : null}
      <div
        className={cn(
          "grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[24rem_minmax(0,1fr)]",
          workspaceView !== "queue" && "hidden",
        )}
      >
        <aside className="min-h-0 border-r">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <div className="text-sm font-medium">队列</div>
            <Badge variant="secondary">{items.length}</Badge>
          </div>
          <div className="h-full overflow-y-auto p-3">
            {isLoading ? (
              <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
                加载中...
              </div>
            ) : error ? (
              <div className="text-destructive flex h-32 items-center justify-center px-4 text-center text-sm">
                {error instanceof Error ? error.message : "加载失败"}
              </div>
            ) : items.length === 0 ? (
              <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
                当前没有复核项
              </div>
            ) : (
              <div className="space-y-2">
                {items.map((item) => (
                  <QueueItemButton
                    key={item.queue_id}
                    item={item}
                    active={activeQueueId === item.queue_id}
                    onClick={() => {
                      setSampleReviewTarget(null);
                      setSelectedQueueId(item.queue_id);
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </aside>

        <main className="min-h-0 overflow-y-auto">
          {!fallbackSelectedItem ? (
            <EmptyDetail />
          ) : contextLoading ? (
            <div className="text-muted-foreground flex min-h-[28rem] items-center justify-center text-sm">
              正在加载上下文...
            </div>
          ) : (
            <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
              <section className="rounded-md border">
                <div className="flex flex-wrap items-start justify-between gap-3 border-b p-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="truncate text-base font-semibold">
                        {queueItemLabel(fallbackSelectedItem)}
                      </h2>
                      <Badge
                        className={priorityClass(fallbackSelectedItem.priority)}
                        variant="outline"
                      >
                        {fallbackSelectedItem.priority}
                      </Badge>
                      <Badge variant="outline">
                        {fallbackSelectedItem.status === "open"
                          ? "待复核"
                          : "已关闭"}
                      </Badge>
                    </div>
                    <p className="text-muted-foreground mt-1 text-sm">
                      {fallbackSelectedItem.alert_id} /{" "}
                      {fallbackSelectedItem.run_id}
                    </p>
                  </div>
                  <div className="flex items-start gap-3">
                    {fallbackSelectedItem.status === "open" && (
                      <Button asChild size="sm" variant="outline">
                        <Link
                          href={`/workspace/agents/soc-triage/chats/new?queue_id=${encodeURIComponent(fallbackSelectedItem.queue_id)}`}
                        >
                          <BotIcon />
                          Lead Agent
                        </Link>
                      </Button>
                    )}
                    <div className="text-muted-foreground text-right text-xs">
                      <div>
                        更新 {formatTime(fallbackSelectedItem.updated_at)}
                      </div>
                      <div>
                        创建 {formatTime(fallbackSelectedItem.created_at)}
                      </div>
                    </div>
                  </div>
                </div>
                <dl className="p-4">
                  <DetailRow
                    label="复核原因"
                    value={fallbackSelectedItem.reason}
                  />
                  <DetailRow
                    label="来源"
                    value={`${fallbackSelectedItem.source_type}${fallbackSelectedItem.source_system ? ` / ${fallbackSelectedItem.source_system}` : ""}`}
                  />
                  <DetailRow
                    label="规则"
                    value={
                      fallbackSelectedItem.rule_code ||
                      fallbackSelectedItem.rule_name
                        ? `${fallbackSelectedItem.rule_code ?? "-"} / ${fallbackSelectedItem.rule_name ?? "-"}`
                        : "-"
                    }
                  />
                  <DetailRow
                    label="定性"
                    value={`${verdictLabel(fallbackSelectedItem.verdict)} / 置信度 ${formatPercent(fallbackSelectedItem.confidence)}`}
                  />
                  <DetailRow
                    label="实体"
                    value={
                      fallbackSelectedItem.entity_keys.length > 0
                        ? fallbackSelectedItem.entity_keys.join(", ")
                        : "-"
                    }
                  />
                  <DetailRow
                    label="摘要"
                    value={fallbackSelectedItem.summary ?? "-"}
                  />
                </dl>
              </section>

              <UnifiedInvestigationViewSection
                view={context?.investigation_view}
              />

              <AuthorizationEnrichmentSection
                records={context?.authorization_enrichments ?? []}
              />

              <DispositionProposalSection
                proposals={context?.disposition_proposals ?? []}
              />

              <DispositionOutcomeCaptureSection
                queueStatus={fallbackSelectedItem.status}
                proposals={context?.disposition_proposals ?? []}
                outcomes={context?.disposition_outcomes ?? []}
                prefill={activeSampleReviewTarget}
              />

              <DispositionOutcomeSection
                outcomes={context?.disposition_outcomes ?? []}
              />

              <ActionEvidenceSection
                evidence={context?.action_evidence ?? []}
              />

              <ExternalDispositionSection
                records={context?.external_dispositions ?? []}
              />

              <RelevantMemorySection result={context?.relevant_memories} />

              <MemoryRetrievalActivationSection
                records={relatedMemoryRecords}
                drafts={memoryRetrievalDrafts}
                isUpdating={updateMemoryRetrievalMutation.isPending}
                onDraftChange={handleMemoryRetrievalDraftChange}
                onAction={(record, action) =>
                  void handleMemoryRetrievalAction(record, action)
                }
              />

              <MemoryCandidateSection
                candidates={context?.memory_candidates ?? []}
                reviewDrafts={memoryReviewDrafts}
                isReviewing={reviewMemoryCandidateMutation.isPending}
                isDraftingLesson={draftMemoryLessonMutation.isPending}
                onReviewDraftChange={handleMemoryReviewDraftChange}
                onReview={(candidate, decision) =>
                  void handleReviewMemoryCandidate(candidate, decision)
                }
                onDraftLesson={(candidate) =>
                  void handleDraftMemoryBusinessLesson(candidate)
                }
              />

              <section className="rounded-md border">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
                  <div className="flex items-center gap-2">
                    <KeyRoundIcon className="text-muted-foreground size-4" />
                    <h3 className="text-sm font-semibold">审批动作</h3>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">{approvalRequests.length}</Badge>
                    {approvalGrant ? (
                      <Badge variant="outline">{approvalGrant.status}</Badge>
                    ) : null}
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-5 p-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                  <div className="space-y-4">
                    <div className="rounded-md border">
                      <div className="flex items-center justify-between gap-3 border-b p-3">
                        <div className="flex items-center gap-2 text-sm font-medium">
                          <InboxIcon className="text-muted-foreground size-4" />
                          审批收件箱
                        </div>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => void refetchApprovalRequests()}
                          disabled={approvalRequestsFetching}
                        >
                          <RefreshCwIcon
                            className={cn(
                              "size-4",
                              approvalRequestsFetching && "animate-spin",
                            )}
                          />
                          刷新
                        </Button>
                      </div>
                      <div className="max-h-64 overflow-y-auto p-2">
                        {approvalRequestsLoading ? (
                          <div className="text-muted-foreground flex h-24 items-center justify-center text-sm">
                            加载中...
                          </div>
                        ) : approvalRequestsError ? (
                          <div className="text-destructive flex h-24 items-center justify-center px-4 text-center text-sm">
                            {approvalRequestsError instanceof Error
                              ? approvalRequestsError.message
                              : "加载失败"}
                          </div>
                        ) : approvalRequests.length === 0 ? (
                          <div className="text-muted-foreground flex h-24 items-center justify-center text-sm">
                            当前没有审批请求
                          </div>
                        ) : (
                          <div className="space-y-2">
                            {approvalRequests.map((request) => {
                              const requestId =
                                request.approval_request_id ??
                                request.permission_decision_id;
                              const active =
                                activeApprovalRequest?.approval_request_id ===
                                  request.approval_request_id ||
                                selectedApprovalRequestId ===
                                  request.approval_request_id;
                              return (
                                <button
                                  key={requestId}
                                  type="button"
                                  onClick={() => {
                                    setSelectedApprovalRequestId(
                                      request.approval_request_id ?? null,
                                    );
                                  }}
                                  className={cn(
                                    "w-full rounded-md border p-3 text-left transition-colors",
                                    "hover:bg-accent focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] focus-visible:outline-none",
                                    active
                                      ? "border-primary bg-accent"
                                      : "border-border bg-background",
                                  )}
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                      <div className="truncate text-sm font-medium">
                                        {approvalRequestLabel(request)}
                                      </div>
                                      <div className="text-muted-foreground mt-1 truncate text-xs">
                                        {request.route} /{" "}
                                        {request.requested_by.actor_id}
                                      </div>
                                    </div>
                                    <div className="flex shrink-0 flex-col items-end gap-1">
                                      <Badge variant="outline">
                                        {request.risk_level}
                                      </Badge>
                                      {request.source_proposal_id ? (
                                        <Badge variant="secondary">
                                          proposal
                                        </Badge>
                                      ) : null}
                                    </div>
                                  </div>
                                  <p className="text-muted-foreground mt-2 line-clamp-2 text-xs">
                                    {request.reason}
                                  </p>
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </div>

                    <ApprovalProposalSummary request={activeApprovalRequest} />

                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="approval-request-json"
                      >
                        审批请求详情
                      </label>
                      <Textarea
                        id="approval-request-json"
                        value={approvalRequestJson}
                        className="min-h-52 resize-none font-mono text-xs"
                        readOnly
                        disabled={approvalRequestLoading}
                      />
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_8rem]">
                      <div className="space-y-2">
                        <label
                          className="text-sm font-medium"
                          htmlFor="approval-reason"
                        >
                          审批原因
                        </label>
                        <Input
                          id="approval-reason"
                          value={approvalReason}
                          onChange={(event) =>
                            setApprovalReason(event.target.value)
                          }
                        />
                      </div>
                      <div className="space-y-2">
                        <label
                          className="text-sm font-medium"
                          htmlFor="approval-expiry"
                        >
                          有效期秒
                        </label>
                        <Input
                          id="approval-expiry"
                          inputMode="numeric"
                          value={approvalExpirySeconds}
                          onChange={(event) =>
                            setApprovalExpirySeconds(event.target.value)
                          }
                        />
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        onClick={() => void handleCreateApprovalGrant()}
                        disabled={
                          createApprovalGrantMutation.isPending ||
                          approvalReason.trim().length === 0 ||
                          activeApprovalRequest?.status !== "pending"
                        }
                      >
                        <KeyRoundIcon className="size-4" />
                        生成审批 token
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          void handleResolveApprovalRequest("reject")
                        }
                        disabled={
                          rejectApprovalRequestMutation.isPending ||
                          approvalReason.trim().length === 0 ||
                          activeApprovalRequest?.status !== "pending"
                        }
                      >
                        <XCircleIcon className="size-4" />
                        驳回
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          void handleResolveApprovalRequest("expire")
                        }
                        disabled={
                          expireApprovalRequestMutation.isPending ||
                          approvalReason.trim().length === 0 ||
                          activeApprovalRequest?.status !== "pending"
                        }
                      >
                        <AlertTriangleIcon className="size-4" />
                        标记过期
                      </Button>
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div className="rounded-md border">
                      <div className="border-b p-3 text-sm font-medium">
                        Grant
                      </div>
                      <pre className="bg-muted max-h-48 overflow-auto p-3 text-xs whitespace-pre-wrap">
                        {prettyJson(approvalGrant)}
                      </pre>
                    </div>
                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="approved-action-payload"
                      >
                        执行 payload JSON
                      </label>
                      <Textarea
                        id="approved-action-payload"
                        value={approvedActionPayloadJson}
                        onChange={(event) =>
                          setApprovedActionPayloadJson(event.target.value)
                        }
                        className="min-h-24 resize-none font-mono text-xs"
                      />
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void handleDryRunApprovedAction()}
                        disabled={
                          !approvalGrant ||
                          dryRunApprovedActionMutation.isPending ||
                          approvalGrant.status !== "approved"
                        }
                      >
                        <FlaskConicalIcon className="size-4" />
                        Dry-run
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => void handleExecuteApprovedAction()}
                        disabled={
                          !approvalGrant ||
                          executeApprovedActionMutation.isPending ||
                          approvalGrant.status !== "approved"
                        }
                      >
                        <PlayCircleIcon className="size-4" />
                        Execute
                      </Button>
                    </div>
                    <div className="rounded-md border">
                      <div className="border-b p-3 text-sm font-medium">
                        Result
                      </div>
                      <pre className="bg-muted max-h-56 overflow-auto p-3 text-xs whitespace-pre-wrap">
                        {prettyJson(approvedActionResult)}
                      </pre>
                    </div>
                  </div>
                </div>
              </section>

              <section className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                <div className="rounded-md border">
                  <div className="flex items-center gap-2 border-b p-4">
                    <CircleIcon className="text-muted-foreground size-4" />
                    <h3 className="text-sm font-semibold">运行上下文</h3>
                  </div>
                  <dl className="p-4">
                    <DetailRow
                      label="状态"
                      value={context?.run.status ?? fallbackSelectedItem.status}
                    />
                    <DetailRow
                      label="Pipeline"
                      value={context?.run.pipeline_version ?? "-"}
                    />
                    <DetailRow
                      label="模型"
                      value={context?.run.model_name ?? "-"}
                    />
                    <DetailRow
                      label="Prompt"
                      value={context?.run.prompt_version ?? "-"}
                    />
                    <DetailRow
                      label="开始"
                      value={formatTime(context?.run.started_at)}
                    />
                    <DetailRow
                      label="结束"
                      value={formatTime(context?.run.ended_at)}
                    />
                  </dl>
                </div>

                <div className="rounded-md border">
                  <div className="flex items-center gap-2 border-b p-4">
                    <AlertTriangleIcon className="text-muted-foreground size-4" />
                    <h3 className="text-sm font-semibold">复核动作</h3>
                  </div>
                  <div className="space-y-4 p-4">
                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="close-reason"
                      >
                        关闭原因
                      </label>
                      <Textarea
                        id="close-reason"
                        value={closeReason}
                        onChange={(event) => setCloseReason(event.target.value)}
                        className="min-h-20 resize-none"
                      />
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void handleClose()}
                        disabled={
                          fallbackSelectedItem.status === "closed" ||
                          closeMutation.isPending ||
                          closeReason.trim().length === 0
                        }
                      >
                        <CheckCircle2Icon className="size-4" />
                        关闭复核项
                      </Button>
                    </div>

                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="correction-reason"
                      >
                        纠正研判
                      </label>
                      <div className="flex flex-wrap gap-2">
                        <Select
                          value={correctedVerdict}
                          onValueChange={(value) =>
                            setCorrectedVerdict(value as SocVerdict)
                          }
                        >
                          <SelectTrigger size="sm" className="w-32">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {VERDICT_OPTIONS.map((option) => (
                              <SelectItem
                                key={option.value}
                                value={option.value}
                              >
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <Textarea
                        id="correction-reason"
                        placeholder="说明为什么需要纠正，后续会进入经验沉淀。"
                        value={correctionReason}
                        onChange={(event) =>
                          setCorrectionReason(event.target.value)
                        }
                        className="min-h-24 resize-none"
                      />
                      <Button
                        size="sm"
                        onClick={() => void handleCorrect()}
                        disabled={
                          correctMutation.isPending ||
                          correctionReason.trim().length === 0
                        }
                      >
                        <XCircleIcon className="size-4" />
                        提交纠正
                      </Button>
                    </div>
                  </div>
                </div>
              </section>

              <section className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                <div className="rounded-md border">
                  <div className="border-b p-4">
                    <h3 className="text-sm font-semibold">相似告警</h3>
                  </div>
                  <div className="divide-y">
                    {(context?.similar_alerts ?? []).length === 0 ? (
                      <div className="text-muted-foreground p-4 text-sm">
                        暂无相似告警。
                      </div>
                    ) : (
                      context?.similar_alerts.map((match) => (
                        <div
                          key={`${match.summary.run_id}-${match.score}`}
                          className="p-4"
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div className="min-w-0 truncate text-sm font-medium">
                              {match.summary.rule_name ??
                                match.summary.rule_code ??
                                match.summary.alert_id}
                            </div>
                            <Badge variant="secondary">
                              {formatPercent(match.score)}
                            </Badge>
                          </div>
                          <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                            {match.summary.summary ?? "-"}
                          </p>
                          <p className="text-muted-foreground mt-2 text-xs">
                            {match.matched_reasons.join(", ") || "-"}
                          </p>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                <div className="rounded-md border">
                  <div className="border-b p-4">
                    <h3 className="text-sm font-semibold">结构化产物</h3>
                  </div>
                  <div className="space-y-3 p-4">
                    <pre className="bg-muted max-h-72 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
                      {prettyJson({
                        entities: context?.run.entities,
                        normalization_report: context?.run.normalization_report,
                        extraction_report: context?.run.extraction_report,
                        fact_reconstruction: context?.run.fact_reconstruction,
                        decision: context?.run.decision,
                      })}
                    </pre>
                  </div>
                </div>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
