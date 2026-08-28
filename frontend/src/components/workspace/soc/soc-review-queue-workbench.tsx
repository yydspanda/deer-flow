"use client";

import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClipboardCheckIcon,
  CircleIcon,
  FilePenLineIcon,
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import {
  SocDispositionSampleInbox,
  type SocDispositionSampleReviewTarget,
} from "@/components/workspace/soc/soc-disposition-sample-inbox";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  SocApiError,
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
    { value: "open", label: "等待确认" },
    { value: "closed", label: "已完成" },
    { value: "all", label: "全部" },
  ];

const MEMORY_CANDIDATE_STATUS_OPTIONS: {
  value: SocMemoryCandidate["status"] | "all";
  label: string;
}[] = [
  { value: "all", label: "全部状态" },
  { value: "pending_review", label: "待审核" },
  { value: "confirmed_candidate", label: "已确认候选" },
  { value: "confirmed", label: "已确认" },
  { value: "rejected", label: "已放弃沉淀" },
  { value: "superseded", label: "已被替代" },
  { value: "expired", label: "已过期" },
  { value: "deprecated", label: "已停用" },
];

const MEMORY_REVISION_ISSUE_LABELS: Record<
  NonNullable<SocMemoryCandidate["revision_lineage"]>["issue_type"],
  string
> = {
  incorrect_conclusion: "结论错误",
  applicability_too_broad: "范围过宽",
  lesson_incomplete: "经验不完整",
};

const VERDICT_OPTIONS: { value: SocVerdict; label: string }[] = [
  { value: "true_positive", label: "真实攻击" },
  { value: "suspicious", label: "可疑，需要继续调查" },
  { value: "false_positive", label: "误报或已知安全行为" },
  { value: "unknown", label: "暂无法判断" },
  { value: "needs_review", label: "等待人工确认" },
];

const ANALYST_VERDICT_OPTIONS = VERDICT_OPTIONS.filter(
  (item) => item.value !== "needs_review",
);

const PRIORITY_LABELS: Record<SocReviewQueueItem["priority"], string> = {
  high: "高优先级",
  medium: "中优先级",
  low: "普通",
};

interface ReviewReasonCopy {
  title: string;
  explanation: string;
  analystAction: string;
}

const REVIEW_REASON_COPY: Record<string, ReviewReasonCopy> = {
  confidence_not_calibrated: {
    title: "置信度尚未校准",
    explanation: "系统给出了判断，但当前分数不能直接当作稳定概率使用。",
    analystAction: "结合调查依据确认最终判断，并记录业务依据。",
  },
  stub_analyzer: {
    title: "当前仅完成基础分析",
    explanation: "本次运行使用了确定性占位分析，没有获得完整模型研判结果。",
    analystAction: "核对原始告警和行为上下文后给出最终判断。",
  },
  raw_confidence_below_threshold: {
    title: "系统判断把握不足",
    explanation: "模型已形成初步结论，但置信度低于当前自动通过门槛。",
    analystAction: "检查关键证据与反证，确认是否需要升级处置。",
  },
  false_positive_requires_confirmation: {
    title: "误报结论需要人工确认",
    explanation: "系统倾向于无风险，但当前治理策略要求由运营人员确认。",
    analystAction: "确认业务事实和适用边界，避免错误放行。",
  },
  uncertain_verdict: {
    title: "系统未形成明确结论",
    explanation: "现有信息同时支持多种判断，系统没有自动选择最终结论。",
    analystAction: "核对主要证据、反证和环境信息后给出最终判断。",
  },
  degraded_message_schema: {
    title: "原始日志结构异常",
    explanation: "日志可以部分使用，但部分字段损坏或未按预期结构提供。",
    analystAction: "优先核对原始日志中的关键行为字段，再确认结论。",
  },
  unsupported_message_schema: {
    title: "当前日志格式尚未适配",
    explanation: "系统保留了原始输入，但无法稳定投影出完整研判字段。",
    analystAction: "依据原始日志完成判断，并将格式问题反馈给归一化运维。",
  },
  high_value_evidence_gap: {
    title: "缺少关键原始证据",
    explanation:
      "系统只获得了部分告警信息，缺少足以确认真实影响的关键行为证据。",
    analystAction:
      "核对原始行为日志、进程、命令行、文件或网络会话后给出最终判断。",
  },
  truncated_analysis_evidence: {
    title: "关键上下文未完整进入分析",
    explanation: "输入内容超过分析预算，部分高价值信息没有得到完整投影。",
    analystAction: "打开完整调查依据，确认被省略内容是否影响当前结论。",
  },
  fact_conflict: {
    title: "告警字段存在实质冲突",
    explanation: "不同证据对攻击方向、角色或关键事实给出了不一致信息。",
    analystAction: "确认可信来源和实际攻击链路后给出最终判断。",
  },
  ungrounded_analysis_evidence: {
    title: "部分证据引用未通过核验",
    explanation: "模型引用的部分内容无法精确对应到本次告警的证据目录。",
    analystAction: "检查被拒绝的引用是否影响核心结论。",
  },
  ungrounded_analysis_reasoning: {
    title: "部分推理依据未通过核验",
    explanation: "模型给出了推理，但相关证据引用不够完整。",
    analystAction: "确认核心推理是否仍被现有证据支持。",
  },
  unproven_outcome_claim: {
    title: "影响结果缺少直接证据",
    explanation: "告警行为可信，但是否已经造成实际影响仍缺少直接依据。",
    analystAction: "核对主机、账号或业务侧结果后确认影响程度。",
  },
  role_verification_challenged: {
    title: "攻击方向复核发现反证",
    explanation: "二次角色核验对攻击者、受害者或网络方向提出了有效异议。",
    analystAction: "核对连接方向和资产角色后确认处置目标。",
  },
  role_verification_unresolved: {
    title: "攻击者与受害者尚未确定",
    explanation: "现有证据不足以稳定裁决网络角色或处置目标。",
    analystAction: "核对网络会话、资产归属和当前场景后确认角色。",
  },
  role_verifier_unavailable: {
    title: "角色复核服务不可用",
    explanation: "主分析已完成，但可选的攻击方向二次复核没有成功运行。",
    analystAction: "人工确认攻击者、受害者和拟处置目标。",
  },
  analysis_output_degraded: {
    title: "模型结果部分降级",
    explanation: "核心判断仍可读取，但部分结构化输出没有通过契约校验。",
    analystAction: "重点确认受影响区块是否改变最终结论或处置目标。",
  },
  analysis_failed: {
    title: "模型研判未完成",
    explanation: "本次模型调用或结果解析失败，系统未获得可用的完整结论。",
    analystAction: "依据现有确定性证据完成判断，必要时重新运行分析。",
  },
};

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
  lessonDetectionScenario: string;
  lessonObservedEvent: string;
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
    lessonDetectionScenario: "",
    lessonObservedEvent: "",
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
    draft.lessonDetectionScenario,
    draft.lessonObservedEvent,
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
  const detectionScenario = draft.lessonDetectionScenario.trim();
  const observedEvent = draft.lessonObservedEvent.trim();
  const businessRationale = reviewedLessonItems(draft.lessonBusinessRationale);
  const generalizationBoundaries = reviewedLessonItems(
    draft.lessonGeneralizationBoundary,
  );
  const invalidationConditions = reviewedLessonItems(
    draft.lessonInvalidationCondition,
  );
  const handlingGuidance = reviewedLessonItems(draft.lessonHandlingGuidance);
  const applicabilityConditions = applicability
    ? Object.entries(applicability.required_facets).map(([key, values]) =>
        memoryApplicabilityCondition(key, values),
      )
    : [];
  if (applicability?.minimum_optional_matches) {
    applicabilityConditions.push(
      `还必须至少匹配 ${applicability.minimum_optional_matches} 组经审核的可选条件。`,
    );
  }
  if (
    detectionScenario.length < 5 ||
    observedEvent.length < 5 ||
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
    schema_version: "soc.memory_business_lesson.v2",
    detection_scenario: detectionScenario,
    observed_event: observedEvent,
    conclusion,
    business_rationale: businessRationale,
    applicability_conditions: applicabilityConditions,
    generalization_boundaries: generalizationBoundaries,
    invalidation_conditions: invalidationConditions,
    handling_guidance: handlingGuidance,
  };
}

function defaultMemoryRetrievalDraft(
  record?: SocMemoryRecord,
): MemoryRetrievalDraft {
  const validUntil = new Date();
  validUntil.setDate(validUntil.getDate() + 90);
  const recordValidUntil = record?.validity.valid_until
    ? new Date(record.validity.valid_until)
    : null;
  if (
    recordValidUntil &&
    recordValidUntil.getTime() > Date.now() &&
    recordValidUntil.getTime() < validUntil.getTime()
  ) {
    validUntil.setTime(recordValidUntil.getTime());
  }
  return {
    reason: "",
    validUntil: validUntil.toISOString().slice(0, 16),
    reviewAfterDays: "30",
  };
}

function memoryConfirmationValidUntil(candidate: SocMemoryCandidate) {
  const preferred = new Date(Date.now() + 60 * 24 * 60 * 60 * 1000);
  if (candidate.source.source_type === "repeated_pattern") {
    return preferred.toISOString();
  }
  const candidateLimit = candidate.validity.valid_until
    ? new Date(candidate.validity.valid_until)
    : null;
  if (
    candidateLimit &&
    !Number.isNaN(candidateLimit.getTime()) &&
    candidateLimit.getTime() > Date.now() + 60_000 &&
    candidateLimit.getTime() < preferred.getTime()
  ) {
    return new Date(candidateLimit.getTime() - 30_000).toISOString();
  }
  return preferred.toISOString();
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

function reviewReasonCodes(item: SocReviewQueueItem) {
  const codes = item.review_reasons?.filter(Boolean) ?? [];
  if (codes.length > 0) return Array.from(new Set(codes));
  return item.reason ? [item.reason] : [];
}

function reviewReasonCopy(item: SocReviewQueueItem): ReviewReasonCopy {
  for (const code of reviewReasonCodes(item)) {
    const copy = REVIEW_REASON_COPY[code];
    if (copy) return copy;
  }
  return {
    title: "系统需要人工确认",
    explanation:
      item.reason || "当前研判没有满足自动完成条件，已转为人工待办。",
    analystAction: "核对调查依据并记录最终判断与业务依据。",
  };
}

function reviewReasonLabel(code: string) {
  return REVIEW_REASON_COPY[code]?.title ?? "其他人工确认原因";
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

function approvalBelongsToReview(
  request: SocAgentApprovalRequest,
  item: SocReviewQueueItem | null,
  proposalIds: Set<string>,
) {
  if (!item) return false;
  const refs = request.context_refs ?? {};
  return (
    refs.queue_id === item.queue_id ||
    refs.run_id === item.run_id ||
    refs.alert_id === item.alert_id ||
    (!!request.source_proposal_id &&
      proposalIds.has(request.source_proposal_id))
  );
}

function hasObjectEntries(value: Record<string, unknown> | null | undefined) {
  return !!value && Object.keys(value).length > 0;
}

function candidateSourceLabel(candidate: SocMemoryCandidate) {
  const source = candidate.source;
  const sourceTypeLabel =
    source.source_type === "memory_revision"
      ? "Memory 修订"
      : source.source_type;
  const refs = [
    sourceTypeLabel,
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
    rejected: "已放弃沉淀",
    superseded: "已被替代",
    expired: "已过期",
    deprecated: "已停用",
  };
  return labels[status] ?? status;
}

function candidateStatusClass(status: SocMemoryCandidate["status"]) {
  if (["pending_review", "confirmed_candidate"].includes(status)) {
    return "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100";
  }
  if (status === "confirmed") {
    return "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-100";
  }
  if (["rejected", "expired", "deprecated"].includes(status)) {
    return "border-zinc-300 bg-zinc-100 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200";
  }
  return "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-100";
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

const MEMORY_FACET_LABELS: Record<string, string> = {
  attack_behavior_family: "攻击行为类型",
  detection_key: "检测键",
  detection_signature: "检测签名",
  rule_code: "规则编码",
  rule_name: "规则名称",
  behavior_fingerprint: "行为指纹",
  behavior_strength: "行为强度",
  behavior_component: "行为特征",
  behavior_component_core: "核心行为",
  behavior_component_strong: "强行为特征",
  behavior_component_weak: "弱行为特征",
  environment: "运行环境",
  source_type: "告警来源类型",
  source_system: "来源系统",
  product: "安全产品",
  scenario_key: "安全场景",
  role_entity: "角色实体",
  entity: "关联实体",
  category: "告警类别",
  severity: "严重级别",
  network_service: "目标网络服务",
  vulnerability_id: "漏洞标识",
  service_uri: "服务地址",
};

const MEMORY_FACET_VALUE_LABELS: Record<string, Record<string, string>> = {
  attack_behavior_family: {
    command_and_control: "命令与控制",
    denial_of_service: "拒绝服务",
    proxy_tunnel_activity: "代理或隧道活动",
    vulnerability_exploitation: "漏洞利用",
  },
  behavior_strength: {
    strong: "强特征",
    weak_only: "仅弱特征",
  },
  environment: {
    dev: "开发环境",
    "dev-corpus-eval": "DEV 告警演练",
    local: "本地环境",
    prd: "生产环境",
    stg: "预发布环境",
  },
  source_type: {
    edr: "终端检测与响应",
    hids: "主机入侵检测",
    ndr: "网络检测与响应",
    nids: "网络入侵检测",
  },
};

function memoryFacetLabel(key: string) {
  return MEMORY_FACET_LABELS[key] ?? key;
}

function memoryFacetValueLabel(key: string, value: string) {
  const label = MEMORY_FACET_VALUE_LABELS[key]?.[value.toLowerCase()];
  return label ? `${label}（${value}）` : value;
}

function memoryApplicabilityCondition(key: string, values: string[]) {
  return `必须匹配「${memoryFacetLabel(key)}（${key}）」：${values
    .map((value) => memoryFacetValueLabel(key, value))
    .join(", ")}`;
}

function memoryLessonDisplayValue(
  section: (typeof MEMORY_LESSON_BLUEPRINT)[number]["key"],
  value: string,
) {
  if (section === "applicability_conditions") {
    const required = /^Required canonical facet ([^:]+):\s*(.+)$/.exec(value);
    if (required) {
      const key = required[1];
      const rawValues = required[2];
      if (!key || !rawValues) return value;
      return memoryApplicabilityCondition(
        key,
        rawValues.split(/,\s*/).filter(Boolean),
      );
    }
    const optional =
      /^At least (\d+) reviewed optional facet groups must also match\.$/.exec(
        value,
      );
    if (optional?.[1]) {
      return `还必须至少匹配 ${optional[1]} 组经审核的可选条件。`;
    }
  }
  if (
    section === "invalidation_conditions" &&
    value === "任一必需 canonical facet 与当前告警不匹配时，该经验失效。"
  ) {
    return "任一系统必需匹配条件与当前告警不一致时，该经验失效。";
  }
  return value;
}

function candidateScopeHighlights(candidate: SocMemoryCandidate) {
  const definitions = [
    { key: "rule_code", label: memoryFacetLabel("rule_code") },
    { key: "product", label: memoryFacetLabel("product") },
    { key: "environment", label: memoryFacetLabel("environment") },
    {
      key: "behavior_component",
      label: memoryFacetLabel("behavior_component"),
    },
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
    key: "detection_scenario",
    order: "01",
    title: "检测场景",
    english: "Detection scenario",
    emphasis: "规则报告",
    description: "说明规则认为发生了什么攻击或异常行为。",
  },
  {
    key: "observed_event",
    order: "02",
    title: "实际事件",
    english: "Observed event",
    emphasis: "真实发生",
    description: "用业务语言说明本组告警实际发生的事件。",
  },
  {
    key: "conclusion",
    order: "03",
    title: "审核结论",
    english: "Reviewed outcome",
    emphasis: "有无风险",
    description: "记录运营专家确认的最终判断及其可复用业务含义。",
  },
  {
    key: "business_rationale",
    order: "04",
    title: "判断依据",
    english: "Reviewed basis",
    emphasis: "事实基础",
    description: "记录支持结论的业务事实与审核依据。",
  },
  {
    key: "applicability_conditions",
    order: "05",
    title: "适用条件",
    english: "Applicability",
    emphasis: "必须匹配",
    description: "决定哪些新告警可以使用，由后端匹配契约生成。",
  },
  {
    key: "generalization_boundaries",
    order: "06",
    title: "允许变化",
    english: "Generalization",
    emphasis: "泛化边界",
    description: "说明 IP、主机、时间等哪些差异不会改变结论。",
  },
  {
    key: "invalidation_conditions",
    order: "07",
    title: "失效与反例",
    english: "Invalidation",
    emphasis: "停止复用",
    description: "出现这些反证时暂停经验，并进入复盘或修订。",
  },
  {
    key: "handling_guidance",
    order: "08",
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
    detection_scenario: lesson.detection_scenario
      ? [lesson.detection_scenario]
      : [],
    observed_event: lesson.observed_event ? [lesson.observed_event] : [],
    conclusion: [lesson.conclusion],
    business_rationale: lesson.business_rationale,
    applicability_conditions: lesson.applicability_conditions,
    generalization_boundaries: lesson.generalization_boundaries,
    invalidation_conditions: lesson.invalidation_conditions,
    handling_guidance: lesson.handling_guidance,
  };

  return (
    <div className="divide-y border-y">
      {MEMORY_LESSON_BLUEPRINT.filter(
        (item) => values[item.key].length > 0,
      ).map((item) => (
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
          {["detection_scenario", "observed_event", "conclusion"].includes(
            item.key,
          ) ? (
            <p className="text-sm leading-6 break-words">
              {values[item.key][0]}
            </p>
          ) : (
            <ul className="space-y-1 text-sm leading-6">
              {values[item.key].map((value) => (
                <li key={value} className="break-words">
                  {memoryLessonDisplayValue(item.key, value)}
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

function MemoryCandidateInventory({
  candidates,
  status,
  isFetching,
  onStatusChange,
  onRefresh,
}: {
  candidates: SocMemoryCandidate[];
  status: SocMemoryCandidate["status"] | "all";
  isFetching: boolean;
  onStatusChange: (status: SocMemoryCandidate["status"] | "all") => void;
  onRefresh: () => void;
}) {
  return (
    <section className="rounded-md border">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div>
          <h3 className="text-sm font-semibold">Candidate 治理台账</h3>
          <p className="text-muted-foreground mt-1 text-xs">
            待审、已确认和历史候选都保留在这里；打开详情查看完整审核对象和治理结果。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={status} onValueChange={onStatusChange}>
            <SelectTrigger size="sm" className="w-36" aria-label="候选状态">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MEMORY_CANDIDATE_STATUS_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="button"
            size="icon-sm"
            variant="outline"
            title="刷新候选台账"
            aria-label="刷新候选台账"
            disabled={isFetching}
            onClick={onRefresh}
          >
            <RefreshCwIcon
              className={cn("size-4", isFetching && "animate-spin")}
            />
          </Button>
        </div>
      </div>
      <div className="divide-y">
        {candidates.length === 0 ? (
          <div className="text-muted-foreground flex min-h-40 items-center justify-center p-4 text-sm">
            当前筛选条件下没有候选经验。
          </div>
        ) : (
          candidates.map((candidate) => {
            const actionable = [
              "pending_review",
              "confirmed_candidate",
            ].includes(candidate.status);
            return (
              <div
                key={candidate.candidate_id}
                className={cn(
                  "grid min-w-0 gap-3 border-l-4 border-l-transparent p-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center",
                  actionable &&
                    "border-l-amber-500 bg-amber-50/40 dark:bg-amber-950/10",
                )}
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      variant="outline"
                      className={candidateStatusClass(candidate.status)}
                    >
                      {candidateStatusLabel(candidate.status)}
                    </Badge>
                    <span className="text-muted-foreground font-mono text-xs break-all">
                      {candidate.candidate_id}
                    </span>
                  </div>
                  <div className="mt-2 text-sm font-semibold break-words">
                    {candidate.summary}
                  </div>
                  <p className="text-muted-foreground mt-1 line-clamp-2 text-sm leading-6 whitespace-pre-wrap">
                    {candidate.content}
                  </p>
                  <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                    <span>{candidateSourceLabel(candidate)}</span>
                    <span>{candidate.evidence_refs.length} 条证据引用</span>
                    <span>更新于 {formatTime(candidate.updated_at)}</span>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant={actionable ? "default" : "secondary"}
                  asChild
                >
                  <Link
                    href={`/workspace/soc/review/memory-candidates/${candidate.candidate_id}`}
                  >
                    {actionable ? (
                      <ShieldCheckIcon className="size-4" />
                    ) : (
                      <SearchCheckIcon className="size-4" />
                    )}
                    {actionable ? "审核并决定" : "查看治理记录"}
                    <ChevronRightIcon className="size-4" />
                  </Link>
                </Button>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function MemoryCandidateProposal({
  candidate,
}: {
  candidate: SocMemoryCandidate;
}) {
  return (
    <div className="mt-4 border-l-4 border-sky-400 bg-sky-50/60 px-4 py-4 dark:border-sky-700 dark:bg-sky-950/20">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold">
            本次审核对象 / Candidate Proposal
          </h4>
          <p className="text-muted-foreground mt-1 text-xs">
            下面是系统提议沉淀的经验正文；审核的是这段内容及其适用范围，不是重新审核整条告警。
          </p>
        </div>
        <Badge variant="outline">{candidate.evidence_refs.length} 条引用</Badge>
      </div>
      <div className="bg-muted/40 mt-3 max-h-96 overflow-y-auto border p-3 text-sm leading-6 break-words whitespace-pre-wrap">
        {candidate.content}
      </div>
      <div className="text-muted-foreground mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs">
        <span>{candidateSourceLabel(candidate)}</span>
        <span>类型：{candidate.candidate_type}</span>
        <span>租户范围：{candidate.tenant_scope}</span>
        <span>有效起始：{formatTime(candidate.validity.valid_from)}</span>
        {candidate.validity.valid_until ? (
          <span>有效截止：{formatTime(candidate.validity.valid_until)}</span>
        ) : null}
      </div>
    </div>
  );
}

function MemoryCandidateGovernanceStatus({
  candidate,
}: {
  candidate: SocMemoryCandidate;
}) {
  const descriptions: Partial<Record<SocMemoryCandidate["status"], string>> = {
    confirmed:
      "该候选已完成审核并沉淀为 Memory。审核后的完整 Business Lesson 在下方展示。",
    rejected: candidate.revision_lineage
      ? "本次 Memory 修订已结束，旧 Memory 保持停用。若后续告警再次证明经验有误，应从那次实际命中记录重新发起修订。"
      : "该候选已被审核人放弃沉淀。候选正文和历史审计仍保留，可显式重新打开审核。",
    superseded: "该候选已被更新版本替代，仅作为历史审计记录保留。",
    expired: "该候选已过有效期，仅作为历史审计记录保留。",
    deprecated: "该候选已停用，不再参与后续 Memory 治理。",
  };
  return (
    <div
      className={cn(
        "mt-4 border-l-4 px-4 py-3",
        candidateStatusClass(candidate.status),
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant="outline"
          className={candidateStatusClass(candidate.status)}
        >
          {candidateStatusLabel(candidate.status)}
        </Badge>
        <span className="text-sm font-medium">当前治理状态</span>
      </div>
      <p className="text-muted-foreground mt-2 text-sm leading-6">
        {descriptions[candidate.status] ?? "该候选当前不可编辑。"}
      </p>
      <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {candidate.reviewed_by ? (
          <span>审核人：{candidate.reviewed_by.actor_id}</span>
        ) : null}
        {candidate.reviewed_at ? (
          <span>审核时间：{formatTime(candidate.reviewed_at)}</span>
        ) : null}
        {candidate.review_reason ? (
          <span className="break-words">
            审核理由：{candidate.review_reason}
          </span>
        ) : null}
      </div>
      {candidate.superseded_by_candidate_id ? (
        <Button className="mt-3" size="sm" variant="outline" asChild>
          <Link
            href={`/workspace/soc/review/memory-candidates/${candidate.superseded_by_candidate_id}`}
          >
            查看替代候选
            <ChevronRightIcon className="size-4" />
          </Link>
        </Button>
      ) : null}
    </div>
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
    reason?: string,
  ) => void;
  onDraftLesson: (candidate: SocMemoryCandidate) => void;
}) {
  const [deprecationTarget, setDeprecationTarget] =
    useState<SocMemoryCandidate | null>(null);
  const [deprecationReason, setDeprecationReason] = useState("");

  const closeDeprecationDialog = () => {
    setDeprecationTarget(null);
    setDeprecationReason("");
  };

  return (
    <section className="rounded-md border">
      <div className="bg-muted/30 flex flex-wrap items-center justify-between gap-3 border-b p-4">
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
                <div
                  className={cn(
                    "-mx-4 -mt-4 flex flex-col gap-3 border-b px-4 py-4 sm:flex-row sm:items-start sm:justify-between",
                    editable
                      ? "border-amber-200 bg-amber-50/50 dark:border-amber-900 dark:bg-amber-950/10"
                      : "bg-muted/20",
                  )}
                >
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
                    <Badge
                      variant="outline"
                      className={candidateStatusClass(candidate.status)}
                    >
                      {candidateStatusLabel(candidate.status)}
                    </Badge>
                    <Badge variant="secondary">
                      {decisionEligible ? "可形成决策经验" : "仅作分析参考"}
                    </Badge>
                  </div>
                </div>

                {candidate.revision_lineage ? (
                  <div className="mt-4 border-l-4 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-100">
                    <div className="flex flex-wrap items-center gap-2 font-semibold">
                      <AlertTriangleIcon className="size-4" />
                      这是现有经验的修订候选
                      <Badge variant="outline">
                        {
                          MEMORY_REVISION_ISSUE_LABELS[
                            candidate.revision_lineage.issue_type
                          ]
                        }
                      </Badge>
                    </div>
                    <p className="mt-2 leading-6">
                      前置经验{" "}
                      {candidate.revision_lineage.predecessor_memory_id} v
                      {candidate.revision_lineage.predecessor_memory_version}
                      已暂停用于新告警。只有本候选审核通过后，系统才会创建替代版本并将旧版本标记为历史记录。
                    </p>
                    <div className="mt-2 grid gap-1 text-xs md:grid-cols-2">
                      <span className="font-mono break-all">
                        source run: {candidate.revision_lineage.source_run_id}
                      </span>
                      <span className="font-mono break-all">
                        source alert:{" "}
                        {candidate.revision_lineage.source_alert_id}
                      </span>
                    </div>
                    <p className="mt-2 text-xs leading-5">
                      纠错依据：{candidate.revision_lineage.reason}
                    </p>
                  </div>
                ) : null}

                <MemoryCandidateProposal candidate={candidate} />

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
                          匹配规则详情 · {applicability.profile_id}@
                          {applicability.profile_version}
                        </span>
                        <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="pb-3">
                      <div className="border bg-zinc-50 px-3 py-3 text-xs">
                        <div className="flex flex-wrap items-center gap-2">
                          <KeyRoundIcon className="size-4" />
                          <span className="font-semibold">系统锁定条件</span>
                          <Badge variant="outline">全部必须命中</Badge>
                        </div>
                        <p className="text-muted-foreground mt-2 leading-5">
                          系统根据候选证据和匹配规则版本{" "}
                          {applicability.profile_id}@
                          {applicability.profile_version}
                          生成这些条件。它们保护规则与核心行为特征，审核页不能删除或改写。
                        </p>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {Object.entries(applicability.required_facets).map(
                            ([key, values]) => (
                              <Badge
                                key={key}
                                variant="outline"
                                className="max-w-full bg-white text-left break-all whitespace-normal"
                              >
                                {memoryFacetLabel(key)}：
                                {values
                                  .slice(0, 3)
                                  .map((value) =>
                                    memoryFacetValueLabel(key, value),
                                  )
                                  .join(", ")}
                              </Badge>
                            ),
                          )}
                        </div>
                      </div>
                      {Object.keys(applicability.optional_facets).length > 0 ? (
                        <div className="mt-3 border px-3 py-3 text-xs">
                          <div className="font-semibold">可选收窄条件</div>
                          <p className="text-muted-foreground mt-1 leading-5">
                            勾选等于增加一项必需条件；取消勾选等于删除本次增加。这里只能收窄，不能扩大，也不能输入
                            系统匹配规则未提供的任意字段。
                          </p>
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
                                    className={cn(
                                      "flex min-w-0 items-start gap-2 border px-3 py-2",
                                      checked &&
                                        "border-sky-300 bg-sky-50 text-sky-950",
                                    )}
                                  >
                                    <input
                                      id={inputId}
                                      type="checkbox"
                                      checked={checked}
                                      disabled={isReviewing || !editable}
                                      className="mt-0.5 size-4 shrink-0"
                                      aria-label={`增加匹配条件 ${memoryFacetLabel(key)}`}
                                      onChange={(event) =>
                                        onReviewDraftChange(candidate, {
                                          promotedFacetKeys: event.target
                                            .checked
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
                                      <span className="font-medium">
                                        {memoryFacetLabel(key)}
                                      </span>
                                      <span className="text-muted-foreground ml-1 break-all">
                                        {values
                                          .slice(0, 3)
                                          .map((value) =>
                                            memoryFacetValueLabel(key, value),
                                          )
                                          .join(", ")}
                                      </span>
                                    </span>
                                  </label>
                                );
                              },
                            )}
                          </div>
                        </div>
                      ) : null}
                      {Object.keys(applicability.excluded_facets).length > 0 ? (
                        <div className="mt-3 border border-red-200 bg-red-50 px-3 py-3 text-xs text-red-950">
                          <div className="font-semibold">排除条件</div>
                          <div className="mt-2 flex flex-wrap gap-2">
                            {Object.entries(applicability.excluded_facets).map(
                              ([key, values]) => (
                                <Badge key={key} variant="outline">
                                  {memoryFacetLabel(key)}：
                                  {values
                                    .slice(0, 3)
                                    .map((value) =>
                                      memoryFacetValueLabel(key, value),
                                    )
                                    .join(", ")}
                                </Badge>
                              ),
                            )}
                          </div>
                        </div>
                      ) : null}
                      <div className="text-muted-foreground mt-3 border-t pt-3 text-xs leading-5">
                        当前匹配公式：
                        <span className="font-medium text-zinc-900">
                          {
                            Object.keys(
                              effectiveApplicability?.required_facets ?? {},
                            ).length
                          }{" "}
                          组必需条件全部命中
                        </span>
                        {effectiveApplicability?.minimum_optional_matches
                          ? `，并至少命中 ${effectiveApplicability.minimum_optional_matches} 组剩余可选条件`
                          : ""}
                        。新字段或新的匹配语义必须由租户匹配规则产生并经过后端契约验证，不能在浏览器中临时扩大适用范围。
                      </div>
                    </CollapsibleContent>
                  </Collapsible>
                ) : null}

                {editable ? (
                  <>
                    <div className="mt-5 border-t-2 pt-4">
                      <div className="text-sm font-semibold">
                        1. 确认业务判断
                      </div>
                      <div className="mt-3 grid gap-3 md:grid-cols-[14rem_minmax(0,1fr)]">
                        <label className="grid content-start gap-1 text-xs font-medium">
                          最终判断
                          <Select
                            value={draft.confirmedVerdict ?? ""}
                            disabled={isReviewing || !editable}
                            onValueChange={(value) =>
                              onReviewDraftChange(candidate, {
                                confirmedVerdict: value as SocVerdict,
                                applyToFutureMatches: false,
                                lessonDetectionScenario: "",
                                lessonObservedEvent: "",
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
                              <SelectItem value="false_positive">
                                误报
                              </SelectItem>
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
                              ? "重新生成研判经验"
                              : "AI 生成研判经验"}
                        </Button>
                      </div>
                    </div>

                    {hasLessonDraft ? (
                      <div className="mt-5 border-t-2 pt-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <div className="text-sm font-semibold">
                              2. 审阅研判经验卡
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
                              检测场景：规则报告了什么
                              <Textarea
                                value={draft.lessonDetectionScenario}
                                disabled={isReviewing || !editable}
                                className="min-h-16 text-xs"
                                onChange={(event) =>
                                  onReviewDraftChange(candidate, {
                                    lessonDetectionScenario: event.target.value,
                                  })
                                }
                              />
                            </label>
                            <label className="grid gap-1 text-xs font-medium md:col-span-2">
                              实际事件：业务上发生了什么
                              <Textarea
                                value={draft.lessonObservedEvent}
                                disabled={isReviewing || !editable}
                                className="min-h-20 text-xs"
                                onChange={(event) =>
                                  onReviewDraftChange(candidate, {
                                    lessonObservedEvent: event.target.value,
                                  })
                                }
                              />
                            </label>
                            <label className="grid gap-1 text-xs font-medium md:col-span-2">
                              审核结论
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
                              判断依据（每行一条）
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
                                    {memoryFacetLabel(key)}：
                                    {values
                                      .slice(0, 3)
                                      .map((value) =>
                                        memoryFacetValueLabel(key, value),
                                      )
                                      .join(", ")}
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
                                    lessonInvalidationCondition:
                                      event.target.value,
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
                      <div
                        className={cn(
                          "mt-5 flex flex-wrap items-center justify-between gap-3 border-y px-3 py-4",
                          draft.applyToFutureMatches
                            ? "border-sky-200 bg-sky-50/70 dark:border-sky-900 dark:bg-sky-950/20"
                            : "bg-muted/30",
                        )}
                      >
                        <div>
                          <div className="text-sm font-semibold">
                            3. 选择未来用途
                          </div>
                          <p className="text-muted-foreground mt-1 max-w-2xl text-xs leading-5">
                            经验开放给新告警后，系统可以找到它；这里决定找到后是仅供模型参考，还是在全部必需条件精确匹配时复用已审核结论。
                          </p>
                        </div>
                        <label className="flex items-center gap-3 border bg-white px-3 py-2 text-xs font-medium dark:bg-zinc-950">
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
                            aria-label="允许精确匹配时参与最终结论"
                          />
                          <span>
                            {draft.applyToFutureMatches
                              ? "精确匹配时复用审核结论"
                              : "仅供研判参考，不改判"}
                          </span>
                        </label>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <MemoryCandidateGovernanceStatus candidate={candidate} />
                )}

                <Collapsible className="mt-4 border-t">
                  <CollapsibleTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      className="group h-auto w-full justify-between rounded-none px-0 py-3 text-xs"
                    >
                      <span>
                        查看证据引用与审计字段 ·{" "}
                        {candidate.evidence_refs.length} 条引用
                      </span>
                      <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="space-y-3 pb-3">
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

                <div className="bg-background/95 sticky bottom-0 z-10 -mx-4 mt-6 flex flex-wrap items-center justify-between gap-3 border-t px-4 py-4 shadow-[0_-8px_20px_-18px_rgba(0,0,0,0.7)] backdrop-blur">
                  <div>
                    <div className="text-sm font-semibold">审核决定</div>
                    <div className="text-muted-foreground mt-0.5 text-xs">
                      主操作会写入治理审计；放弃或停用不会改写原始告警结论。
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {candidate.status === "rejected" &&
                    candidate.revision_lineage ? (
                      <span className="text-muted-foreground max-w-sm text-right text-xs leading-5">
                        修订版本已冻结；请从新的实际误命中告警重新发起。
                      </span>
                    ) : candidate.status === "rejected" ? (
                      <Button
                        size="sm"
                        disabled={isReviewing}
                        title="保留原驳回审计，并将候选返回待审核状态"
                        onClick={() => onReview(candidate, "reopen")}
                      >
                        <RefreshCwIcon className="size-4" />
                        重新打开审核
                      </Button>
                    ) : editable ? (
                      <>
                        <Button
                          size="sm"
                          disabled={
                            isReviewing ||
                            draft.confirmedVerdict === null ||
                            !reviewedLesson
                          }
                          onClick={() => onReview(candidate, "confirm")}
                        >
                          <CheckCircle2Icon className="size-4" />
                          确认并启用经验
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={isReviewing}
                          title="仅放弃这条 Memory 候选，不改变告警的最终判断"
                          onClick={() => onReview(candidate, "reject")}
                        >
                          <XCircleIcon className="size-4" />
                          放弃沉淀此候选
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={isReviewing}
                          onClick={() => onReview(candidate, "expire")}
                        >
                          过期
                        </Button>
                      </>
                    ) : candidate.status === "confirmed" ? (
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={isReviewing}
                        onClick={() => {
                          setDeprecationTarget(candidate);
                          setDeprecationReason("");
                        }}
                      >
                        <XCircleIcon className="size-4" />
                        废止这条经验
                      </Button>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
      <Dialog
        open={deprecationTarget !== null}
        onOpenChange={(open) => {
          if (!open && !isReviewing) closeDeprecationDialog();
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>废止这条经验</DialogTitle>
            <DialogDescription>
              这不是临时暂停。关联 Candidate 和 Memory
              将被标记为已废止，后续告警将无法再检索或复用它；历史告警、使用记录与审计证据仍会保留。
            </DialogDescription>
          </DialogHeader>
          <label className="grid gap-2 text-sm">
            <span className="font-medium">废止原因</span>
            <Textarea
              value={deprecationReason}
              onChange={(event) => setDeprecationReason(event.target.value)}
              placeholder="说明这条经验为什么已经错误、过时或不应继续使用"
              rows={4}
              disabled={isReviewing}
            />
            <span className="text-muted-foreground text-xs">
              至少 10 个字符；该说明会进入治理审计。
            </span>
          </label>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={isReviewing}
              onClick={closeDeprecationDialog}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={isReviewing || deprecationReason.trim().length < 10}
              onClick={() => {
                if (!deprecationTarget) return;
                onReview(
                  deprecationTarget,
                  "deprecate",
                  deprecationReason.trim(),
                );
                closeDeprecationDialog();
              }}
            >
              确认废止
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
          当前告警没有历史经验匹配结果。
        </div>
      ) : (
        <div className="divide-y">
          <div className="text-muted-foreground grid gap-2 p-4 text-xs sm:grid-cols-4">
            <span>候选 {result.total_candidate_count}</span>
            <span>暂停使用 {result.skipped_retrieval_disabled}</span>
            <span>缺少使用审批 {result.skipped_ungoverned_activation}</span>
            <span>使用期已过 {result.skipped_activation_expired}</span>
            <span>逾期未复核 {result.skipped_review_overdue}</span>
            <span>状态过滤 {result.skipped_status}</span>
            <span>强锚点过滤 {result.skipped_missing_strong_anchor}</span>
            <span>适用范围过滤 {result.skipped_not_applicable}</span>
            <span>低分过滤 {result.skipped_below_min_score}</span>
            <span>仅供参考 {result.returned_context_only_count}</span>
          </div>
          {matches.length === 0 ? (
            <div className="text-muted-foreground p-4 text-sm">
              没有找到可用于当前告警的已确认经验。
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
                    <Badge variant="outline">已开放给新告警</Badge>
                    {match.applicability_report ? (
                      <Badge
                        variant={
                          match.applicability_report.context_only_allowed
                            ? "outline"
                            : "secondary"
                        }
                      >
                        {match.applicability_report.context_only_allowed
                          ? "仅供研判参考"
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
    record: SocMemoryRecord,
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
          <h3 className="text-sm font-semibold">已沉淀 Memory / 检索治理</h3>
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
              drafts[record.memory_id] ?? defaultMemoryRetrievalDraft(record);
            const nextAction: SocMemoryRetrievalActivationAction =
              record.retrieval_enabled ? "disable" : "enable";
            const reviewAfterDays = Number(draft.reviewAfterDays);
            const invalidEnable =
              nextAction === "enable" &&
              (!draft.validUntil ||
                !Number.isInteger(reviewAfterDays) ||
                reviewAfterDays < 1);
            const retrievalMutable = record.status === "confirmed";
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
                        ? "已开放给新告警"
                        : "暂停用于新告警"}
                    </Badge>
                    <Badge
                      variant={
                        record.decision_directive ? "secondary" : "outline"
                      }
                    >
                      {record.decision_directive
                        ? "精确匹配可复用结论"
                        : "仅供研判参考"}
                    </Badge>
                    <Button size="sm" variant="outline" asChild>
                      <Link
                        href={`/workspace/soc/memory/records/${encodeURIComponent(record.memory_id)}`}
                      >
                        <FilePenLineIcon className="size-4" />
                        查看 / 修订经验
                      </Link>
                    </Button>
                  </div>
                </div>
                <p className="text-muted-foreground mt-2 text-xs leading-5">
                  使用状态决定新告警能否找到这条经验；未来用途决定它在满足全部适用条件后，是仅作研判参考，还是可以复用已审核结论。
                </p>
                <div className="mt-3 border-l-2 border-sky-500 pl-3 text-xs leading-5">
                  <div className="flex items-center gap-1.5 font-semibold text-sky-800 dark:text-sky-200">
                    <CheckCircle2Icon className="size-3.5" />
                    当前使用状态依据
                  </div>
                  <p className="text-foreground mt-1">
                    {record.retrieval_reason ??
                      "当前状态已由候选审核或最近一次使用状态操作确认。"}
                  </p>
                  <p className="text-muted-foreground mt-1">
                    下方说明只用于下一次暂停或重新开放，不是当前状态生效的前置条件。
                  </p>
                </div>
                <div className="mt-4">
                  {record.business_lesson ? (
                    <MemoryLessonReadView lesson={record.business_lesson} />
                  ) : (
                    <div className="border-y py-3">
                      <div className="text-xs font-semibold">
                        已确认经验正文
                      </div>
                      <p className="text-muted-foreground mt-2 text-sm leading-6 whitespace-pre-wrap">
                        {record.content}
                      </p>
                      <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                        该历史记录没有结构化 Business
                        Lesson，请在下次复核时补齐。
                      </p>
                    </div>
                  )}
                </div>
                {retrievalMutable ? (
                  <Collapsible className="mt-4">
                    <CollapsibleTrigger asChild>
                      <Button
                        size="sm"
                        variant="outline"
                        className="group"
                        disabled={isUpdating}
                      >
                        <PowerIcon className="size-4" />
                        管理使用状态
                        <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="mt-3 border-t pt-3">
                      <div className="mb-3">
                        <div className="text-sm font-semibold">
                          {nextAction === "disable"
                            ? "暂停用于新告警"
                            : "重新开放给新告警"}
                        </div>
                        <p className="text-muted-foreground mt-1 text-xs">
                          {nextAction === "disable"
                            ? "执行后新告警将完全检索不到这条经验；需要时仍可重新开放。"
                            : "执行后新告警可以找到这条经验，并按上方标明的未来用途使用。"}
                        </p>
                      </div>
                      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem_8rem_auto] lg:items-end">
                        <label className="grid gap-1 text-xs">
                          <span className="text-muted-foreground">
                            {nextAction === "disable"
                              ? "暂停原因（必填）"
                              : "重新开放原因（必填）"}
                          </span>
                          <Input
                            value={draft.reason}
                            placeholder={
                              nextAction === "disable"
                                ? "说明为什么暂停用于新告警"
                                : "说明为什么重新开放给新告警"
                            }
                            onChange={(event) =>
                              onDraftChange(
                                record,
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
                                record,
                                "validUntil",
                                event.target.value,
                              )
                            }
                            disabled={isUpdating || nextAction === "disable"}
                          />
                        </label>
                        <label className="grid gap-1 text-xs">
                          <span className="text-muted-foreground">
                            复核天数
                          </span>
                          <Input
                            type="number"
                            min={1}
                            max={365}
                            value={draft.reviewAfterDays}
                            onChange={(event) =>
                              onDraftChange(
                                record,
                                "reviewAfterDays",
                                event.target.value,
                              )
                            }
                            disabled={isUpdating || nextAction === "disable"}
                          />
                        </label>
                        <Button
                          size="sm"
                          variant={
                            nextAction === "enable" ? "default" : "outline"
                          }
                          disabled={
                            isUpdating || !draft.reason.trim() || invalidEnable
                          }
                          onClick={() => onAction(record, nextAction)}
                        >
                          <PowerIcon className="size-4" />
                          {nextAction === "enable"
                            ? "确认重新开放"
                            : "确认暂停"}
                        </Button>
                      </div>
                    </CollapsibleContent>
                  </Collapsible>
                ) : (
                  <div className="text-muted-foreground mt-4 border-y py-3 text-xs">
                    历史经验为只读状态，不能修改新告警使用状态。
                  </div>
                )}
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
  const reason = reviewReasonCopy(item);
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
          {PRIORITY_LABELS[item.priority]}
        </Badge>
      </div>
      <div className="mt-3 flex items-start gap-2 text-xs">
        {item.status === "open" ? (
          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
        ) : (
          <CheckCircle2Icon className="mt-0.5 size-3.5 shrink-0 text-emerald-600" />
        )}
        <span className="line-clamp-2 font-medium">{reason.title}</span>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 text-xs">
        <span className="text-muted-foreground">
          {formatTime(item.updated_at)}
        </span>
        <span className="text-muted-foreground">
          系统判断：{verdictLabel(item.verdict)}
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
        <p className="text-sm font-medium">选择一条告警待办</p>
        <p className="text-muted-foreground mt-1 text-sm">
          查看为什么需要人工处理，并记录最终判断。
        </p>
      </div>
    </div>
  );
}

function ReviewDetailGroup({
  icon: Icon,
  title,
  description,
  defaultOpen = false,
  forceOpen = false,
  children,
}: {
  icon: typeof SearchCheckIcon;
  title: string;
  description: string;
  defaultOpen?: boolean;
  forceOpen?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Collapsible
      defaultOpen={defaultOpen}
      {...(forceOpen ? { open: true } : {})}
      className="border-y"
    >
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          className="group h-auto w-full justify-between rounded-none px-0 py-4 text-left"
        >
          <span className="flex min-w-0 items-start gap-3">
            <span className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-md">
              <Icon className="size-4" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold">{title}</span>
              <span className="text-muted-foreground mt-0.5 block text-xs font-normal whitespace-normal">
                {description}
              </span>
            </span>
          </span>
          <ChevronDownIcon className="text-muted-foreground size-4 shrink-0 transition-transform group-data-[state=open]:rotate-180" />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-5 pb-5">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

export function SocReviewQueueWorkbench({
  initialQueueId,
  initialCandidateId,
  initialView,
}: {
  initialQueueId?: string;
  initialCandidateId?: string;
  initialView?: "queue" | "memory" | "sample";
}) {
  const [workspaceView, setWorkspaceView] = useState<
    "queue" | "memory" | "sample"
  >(initialView ?? (initialCandidateId ? "memory" : "queue"));
  const [statusFilter, setStatusFilter] = useState<
    SocReviewQueueStatus | "all"
  >(initialQueueId ? "all" : "open");
  const [memoryCandidateStatusFilter, setMemoryCandidateStatusFilter] =
    useState<SocMemoryCandidate["status"] | "all">("all");
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(
    initialQueueId ?? null,
  );
  const [sampleReviewTarget, setSampleReviewTarget] =
    useState<SocDispositionSampleReviewTarget | null>(null);
  const [analystReason, setAnalystReason] = useState("");
  const [analystVerdict, setAnalystVerdict] =
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
    enabled: workspaceView === "queue",
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
    workspaceView === "queue"
      ? (selectedItem?.queue_id ?? fallbackSelectedItem?.queue_id)
      : undefined;
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
    candidates: listedMemoryCandidates,
    isLoading: listedMemoryCandidatesLoading,
    isFetching: listedMemoryCandidatesFetching,
    error: listedMemoryCandidatesError,
    refetch: refetchListedMemoryCandidates,
  } = useSocMemoryCandidates({
    status:
      memoryCandidateStatusFilter === "all"
        ? null
        : memoryCandidateStatusFilter,
    limit: 50,
    enabled: workspaceView === "memory" && !initialCandidateId,
  });
  const standaloneMemoryCandidates = useMemo(
    () =>
      focusedMemoryCandidate
        ? [focusedMemoryCandidate]
        : listedMemoryCandidates,
    [focusedMemoryCandidate, listedMemoryCandidates],
  );
  const activeMemoryCandidates = useMemo(
    () =>
      workspaceView === "memory"
        ? standaloneMemoryCandidates
        : (context?.memory_candidates ?? []),
    [context?.memory_candidates, standaloneMemoryCandidates, workspaceView],
  );
  const { records: memoryRecords } = useSocMemoryRecords({
    status: null,
    sourceCandidateId:
      workspaceView === "memory" ? initialCandidateId : undefined,
    limit: workspaceView === "memory" ? 20 : 200,
    enabled:
      workspaceView === "queue" ||
      (workspaceView === "memory" && !!initialCandidateId),
  });
  const relatedMemoryRecords = useMemo(() => {
    const candidateIds = new Set(
      activeMemoryCandidates.map((candidate) => candidate.candidate_id),
    );
    return memoryRecords.filter((record) =>
      candidateIds.has(record.source_candidate_id),
    );
  }, [activeMemoryCandidates, memoryRecords]);
  const {
    requests: approvalRequests,
    isLoading: approvalRequestsLoading,
    isFetching: approvalRequestsFetching,
    error: approvalRequestsError,
    refetch: refetchApprovalRequests,
  } = useSocApprovalRequests({
    status: "pending",
    limit: 50,
    enabled: workspaceView === "queue",
  });
  const scopedApprovalRequests = useMemo(() => {
    const proposalIds = new Set(
      (context?.disposition_proposals ?? []).map(
        (proposal) => proposal.proposal_id,
      ),
    );
    return approvalRequests.filter((request) =>
      approvalBelongsToReview(request, fallbackSelectedItem, proposalIds),
    );
  }, [approvalRequests, context?.disposition_proposals, fallbackSelectedItem]);
  const fallbackSelectedApprovalRequest = useMemo(
    () =>
      scopedApprovalRequests.find(
        (request) => request.approval_request_id === selectedApprovalRequestId,
      ) ??
      scopedApprovalRequests[0] ??
      null,
    [scopedApprovalRequests, selectedApprovalRequestId],
  );
  const activeApprovalRequestId =
    fallbackSelectedApprovalRequest?.approval_request_id;
  const {
    request: selectedApprovalRequest,
    isLoading: approvalRequestLoading,
  } = useSocApprovalRequest(
    workspaceView === "queue" ? activeApprovalRequestId : null,
  );
  const activeApprovalRequest =
    selectedApprovalRequest ?? fallbackSelectedApprovalRequest;
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
    const firstRequestId = scopedApprovalRequests[0]?.approval_request_id;
    const selectionIsScoped = scopedApprovalRequests.some(
      (request) => request.approval_request_id === selectedApprovalRequestId,
    );
    if (!selectionIsScoped)
      setSelectedApprovalRequestId(firstRequestId ?? null);
  }, [scopedApprovalRequests, selectedApprovalRequestId]);

  useEffect(() => {
    if (!activeApprovalRequest) return;
    setApprovalRequestJson(JSON.stringify(activeApprovalRequest, null, 2));
    setApprovalGrant(null);
    setApprovedActionResult(null);
  }, [activeApprovalRequest]);

  const handleRecordAnalystDecision = async () => {
    const runId = context?.run.run_id ?? fallbackSelectedItem?.run_id;
    if (!runId || analystReason.trim().length === 0) return;
    try {
      await correctMutation.mutateAsync({
        runId,
        request: {
          verdict: analystVerdict,
          reason: analystReason.trim(),
        },
      });
      toast.success("最终判断已记录，告警待办已完成");
      setAnalystReason("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "最终判断记录失败");
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
    explicitReason?: string,
  ) => {
    const draft =
      memoryReviewDrafts[candidate.candidate_id] ??
      defaultMemoryCandidateReviewDraft(candidate);
    const reviewerVerdict = draft.confirmedVerdict;
    if (decision === "deprecate" && (explicitReason?.trim().length ?? 0) < 10) {
      toast.error("请填写至少 10 个字符的废止原因");
      return;
    }
    const reason =
      decision === "deprecate" && explicitReason?.trim()
        ? explicitReason.trim()
        : decision === "reject"
          ? "审核人决定放弃沉淀该候选，未形成可复用 Memory。"
          : decision === "reopen"
            ? "审核人重新打开此前被放弃的候选，返回待审核状态。"
            : decision === "confirm" && reviewerVerdict
              ? `审核人确认研判经验，最终判断为${verdictLabel(reviewerVerdict)}；未来用途为${draft.applyToFutureMatches ? "精确匹配时复用结论" : "仅作研判参考"}。`
              : `审核人执行候选状态变更：${decision}。`;
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
          ...(decision === "confirm"
            ? {
                confirmed_verdict: reviewerVerdict,
                activate_retrieval: true,
                activation_valid_until: memoryConfirmationValidUntil(candidate),
                activation_review_after_days: 30,
              }
            : {}),
          ...(decision === "confirm" && draft.applyToFutureMatches
            ? {
                apply_to_future_matches: true,
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
      toast.success(
        decision === "reopen"
          ? "候选已重新打开，可以继续审核"
          : "候选记忆已更新",
      );
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
        lessonDetectionScenario: lesson.detection_scenario ?? "",
        lessonObservedEvent: lesson.observed_event ?? "",
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
    record: SocMemoryRecord,
    field: keyof MemoryRetrievalDraft,
    value: string,
  ) => {
    setMemoryRetrievalDrafts((current) => ({
      ...current,
      [record.memory_id]: {
        ...(current[record.memory_id] ?? defaultMemoryRetrievalDraft(record)),
        [field]: value,
      },
    }));
  };

  const handleMemoryRetrievalAction = async (
    record: SocMemoryRecord,
    action: SocMemoryRetrievalActivationAction,
  ) => {
    const draft =
      memoryRetrievalDrafts[record.memory_id] ??
      defaultMemoryRetrievalDraft(record);
    const reason = draft.reason.trim();
    if (!reason) {
      toast.error("请填写使用状态变更说明");
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
      setMemoryRetrievalDrafts((current) => {
        const next = { ...current };
        delete next[record.memory_id];
        return next;
      });
      toast.success(enabling ? "确认记忆检索已启用" : "确认记忆检索已停用");
    } catch (err) {
      if (err instanceof SocApiError && err.status === 409) {
        setMemoryRetrievalDrafts((current) => {
          const next = { ...current };
          delete next[record.memory_id];
          return next;
        });
        toast.info("Memory 已被更新，页面已同步到最新版本");
        return;
      }
      toast.error(err instanceof Error ? err.message : "记忆检索状态更新失败");
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={
          workspaceView === "memory"
            ? LibraryBigIcon
            : workspaceView === "sample"
              ? FlaskConicalIcon
              : ShieldCheckIcon
        }
        title={
          workspaceView === "memory"
            ? "经验审核"
            : workspaceView === "sample"
              ? "质量评测"
              : "需人工介入"
        }
        description={
          workspaceView === "memory"
            ? "审核待沉淀经验；确认前不会影响新告警"
            : workspaceView === "sample"
              ? "抽样评估处置建议，不属于日常告警队列"
              : "只处理 Runtime 无法解决的关键事实冲突"
        }
        actions={
          <>
            {workspaceView === "queue" ? (
              <>
                <Button variant="outline" size="sm" asChild>
                  <Link href="/workspace/soc/alerts">
                    <ChevronLeftIcon className="size-4" />
                    返回告警研判
                  </Link>
                </Button>
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
            ) : workspaceView === "memory" ? (
              <Button variant="outline" size="sm" asChild>
                <Link href="/workspace/soc/memory">
                  <ChevronLeftIcon className="size-4" />
                  返回经验中心
                </Link>
              </Button>
            ) : null}
          </>
        }
      />

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
                  <h2 className="text-sm font-semibold">经验候选审核</h2>
                  <p className="text-muted-foreground mt-1 text-sm">
                    决定跨告警经验是否值得沉淀，以及未来新告警可以如何使用。
                  </p>
                </div>
                {initialCandidateId ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="ghost" size="sm" asChild>
                      <Link href="/workspace/soc/review/memory-candidates">
                        <ChevronLeftIcon className="size-4" />
                        返回候选台账
                      </Link>
                    </Button>
                    <Badge variant="outline">{initialCandidateId}</Badge>
                  </div>
                ) : (
                  <Badge variant="secondary">
                    {standaloneMemoryCandidates.length} 条待审核经验
                  </Badge>
                )}
              </div>
            </section>

            {(
              initialCandidateId
                ? focusedMemoryCandidateLoading
                : listedMemoryCandidatesLoading
            ) ? (
              <div className="text-muted-foreground flex min-h-48 items-center justify-center border text-sm">
                正在加载待审核经验...
              </div>
            ) : (
                initialCandidateId
                  ? focusedMemoryCandidateError
                  : listedMemoryCandidatesError
              ) ? (
              <div className="border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                {(initialCandidateId
                  ? focusedMemoryCandidateError
                  : listedMemoryCandidatesError) instanceof Error
                  ? (initialCandidateId
                      ? focusedMemoryCandidateError
                      : listedMemoryCandidatesError
                    )?.message
                  : "待审核经验加载失败"}
              </div>
            ) : !initialCandidateId ? (
              <MemoryCandidateInventory
                candidates={standaloneMemoryCandidates}
                status={memoryCandidateStatusFilter}
                isFetching={listedMemoryCandidatesFetching}
                onStatusChange={setMemoryCandidateStatusFilter}
                onRefresh={() => void refetchListedMemoryCandidates()}
              />
            ) : standaloneMemoryCandidates.length === 0 ? (
              <div className="text-muted-foreground flex min-h-48 items-center justify-center border text-sm">
                未找到该待审核经验。
              </div>
            ) : (
              <>
                <MemoryCandidateSection
                  candidates={standaloneMemoryCandidates}
                  reviewDrafts={memoryReviewDrafts}
                  isReviewing={reviewMemoryCandidateMutation.isPending}
                  isDraftingLesson={draftMemoryLessonMutation.isPending}
                  onReviewDraftChange={handleMemoryReviewDraftChange}
                  onReview={(candidate, decision, reason) =>
                    void handleReviewMemoryCandidate(
                      candidate,
                      decision,
                      reason,
                    )
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
            <div>
              <div className="text-sm font-medium">告警待办</div>
              <div className="text-muted-foreground mt-0.5 text-xs">
                按更新时间排序
              </div>
            </div>
            <Badge variant="secondary">{items.length} 条</Badge>
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
                当前没有等待人工确认的告警
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
              <section className="overflow-hidden rounded-md border">
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
                        {PRIORITY_LABELS[fallbackSelectedItem.priority]}
                      </Badge>
                      <Badge
                        variant={
                          fallbackSelectedItem.status === "open"
                            ? "secondary"
                            : "outline"
                        }
                      >
                        {fallbackSelectedItem.status === "open"
                          ? "等待人工确认"
                          : "已完成"}
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
                          交给 Lead Agent 调查
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
                <div className="grid divide-y lg:grid-cols-[1.15fr_0.85fr_1fr] lg:divide-x lg:divide-y-0">
                  <div className="p-4">
                    <div className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                      <AlertTriangleIcon className="size-4 text-amber-600" />
                      为什么需要你处理
                    </div>
                    <h3 className="mt-3 text-base font-semibold">
                      {reviewReasonCopy(fallbackSelectedItem).title}
                    </h3>
                    <p className="text-muted-foreground mt-2 text-sm leading-6">
                      {reviewReasonCopy(fallbackSelectedItem).explanation}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {reviewReasonCodes(fallbackSelectedItem).map((code) => (
                        <Badge key={code} variant="outline">
                          {reviewReasonLabel(code)}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <div className="p-4">
                    <div className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                      <ShieldAlertIcon className="size-4" />
                      系统当前判断
                    </div>
                    <div className="mt-3 text-lg font-semibold">
                      {verdictLabel(fallbackSelectedItem.verdict)}
                    </div>
                    <div className="text-muted-foreground mt-1 text-sm">
                      置信度 {formatPercent(fallbackSelectedItem.confidence)}
                    </div>
                    <p className="mt-3 text-sm leading-6">
                      {fallbackSelectedItem.summary ?? "系统未提供结论摘要。"}
                    </p>
                  </div>
                  <div className="p-4">
                    <div className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                      <ClipboardCheckIcon className="size-4" />
                      你需要完成什么
                    </div>
                    <p className="mt-3 text-sm leading-6 font-medium">
                      {reviewReasonCopy(fallbackSelectedItem).analystAction}
                    </p>
                    <ol className="text-muted-foreground mt-3 space-y-2 text-xs leading-5">
                      <li>1. 核对下方调查依据。</li>
                      <li>2. 选择最终判断并填写业务依据。</li>
                      <li>3. 提交后自动完成待办并保留审计记录。</li>
                    </ol>
                  </div>
                </div>
                <dl className="bg-muted/30 grid border-t px-4 py-2 text-xs md:grid-cols-2 md:gap-x-8">
                  <DetailRow
                    label="告警来源"
                    value={`${fallbackSelectedItem.source_type}${fallbackSelectedItem.source_system ? ` / ${fallbackSelectedItem.source_system}` : ""}`}
                  />
                  <DetailRow
                    label="检测规则"
                    value={
                      fallbackSelectedItem.rule_code ||
                      fallbackSelectedItem.rule_name
                        ? `${fallbackSelectedItem.rule_code ?? "-"} / ${fallbackSelectedItem.rule_name ?? "-"}`
                        : "-"
                    }
                  />
                </dl>
              </section>

              <section
                className={cn(
                  "rounded-md border border-l-4",
                  fallbackSelectedItem.status === "open"
                    ? "border-l-amber-500"
                    : "border-l-emerald-500",
                )}
              >
                <div className="flex flex-wrap items-start justify-between gap-3 border-b p-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <ClipboardCheckIcon className="size-4" />
                      <h3 className="text-sm font-semibold">
                        {fallbackSelectedItem.status === "open"
                          ? "记录最终判断"
                          : "待办处理结果"}
                      </h3>
                    </div>
                    <p className="text-muted-foreground mt-1 text-sm">
                      {fallbackSelectedItem.status === "open"
                        ? "同意系统判断也需要选择相同结论并说明依据；提交后服务端会自动完成此待办。"
                        : "该待办已经完成，历史判断和处理依据仍可审计。"}
                    </p>
                  </div>
                  {fallbackSelectedItem.status === "closed" ? (
                    <Badge
                      className="border-emerald-200 bg-emerald-50 text-emerald-700"
                      variant="outline"
                    >
                      <CheckCircle2Icon className="size-3.5" />
                      已完成
                    </Badge>
                  ) : null}
                </div>
                {fallbackSelectedItem.status === "open" ? (
                  <div className="grid gap-4 p-4 lg:grid-cols-[16rem_minmax(0,1fr)_auto] lg:items-end">
                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="analyst-verdict"
                      >
                        最终判断
                      </label>
                      <Select
                        value={analystVerdict}
                        onValueChange={(value) =>
                          setAnalystVerdict(value as SocVerdict)
                        }
                      >
                        <SelectTrigger
                          id="analyst-verdict"
                          className="w-full"
                          aria-label="最终判断"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {ANALYST_VERDICT_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="analyst-reason"
                      >
                        判断依据
                      </label>
                      <Textarea
                        id="analyst-reason"
                        aria-label="最终判断依据"
                        placeholder="说明支持该结论的业务事实、调查结果或仍缺少的证据。"
                        value={analystReason}
                        onChange={(event) =>
                          setAnalystReason(event.target.value)
                        }
                        className="min-h-20 resize-none"
                      />
                    </div>
                    <Button
                      onClick={() => void handleRecordAnalystDecision()}
                      disabled={
                        correctMutation.isPending ||
                        analystReason.trim().length === 0
                      }
                    >
                      <CheckCircle2Icon className="size-4" />
                      提交并完成待办
                    </Button>
                  </div>
                ) : (
                  <dl className="p-4">
                    <DetailRow
                      label="完成时间"
                      value={formatTime(fallbackSelectedItem.closed_at)}
                    />
                    <DetailRow
                      label="处理人"
                      value={fallbackSelectedItem.closed_by?.actor_id ?? "-"}
                    />
                    <DetailRow
                      label="处理依据"
                      value={fallbackSelectedItem.close_reason ?? "-"}
                    />
                  </dl>
                )}
              </section>

              <ReviewDetailGroup
                icon={SearchCheckIcon}
                title="研判依据"
                description="查看当前告警事实、调查补充和系统实际使用的历史经验。"
                defaultOpen
              >
                <UnifiedInvestigationViewSection
                  view={context?.investigation_view}
                />

                <AuthorizationEnrichmentSection
                  records={context?.authorization_enrichments ?? []}
                />

                <ActionEvidenceSection
                  evidence={context?.action_evidence ?? []}
                />

                <RelevantMemorySection result={context?.relevant_memories} />
              </ReviewDetailGroup>

              <ReviewDetailGroup
                icon={LibraryBigIcon}
                title="处置记录与经验沉淀"
                description="查看处置建议、执行结果和候选经验；这些内容不会代替上方的最终判断。"
                defaultOpen={!!activeSampleReviewTarget}
                forceOpen={!!activeSampleReviewTarget}
              >
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

                <ExternalDispositionSection
                  records={context?.external_dispositions ?? []}
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

                <MemoryCandidateSection
                  candidates={context?.memory_candidates ?? []}
                  reviewDrafts={memoryReviewDrafts}
                  isReviewing={reviewMemoryCandidateMutation.isPending}
                  isDraftingLesson={draftMemoryLessonMutation.isPending}
                  onReviewDraftChange={handleMemoryReviewDraftChange}
                  onReview={(candidate, decision, reason) =>
                    void handleReviewMemoryCandidate(
                      candidate,
                      decision,
                      reason,
                    )
                  }
                  onDraftLesson={(candidate) =>
                    void handleDraftMemoryBusinessLesson(candidate)
                  }
                />
              </ReviewDetailGroup>

              <ReviewDetailGroup
                icon={KeyRoundIcon}
                title="动作审批"
                description="只显示与当前告警关联的高风险动作请求；它与告警最终判断是两个独立任务。"
              >
                <section className="rounded-md border">
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
                    <div className="flex items-center gap-2">
                      <KeyRoundIcon className="text-muted-foreground size-4" />
                      <h3 className="text-sm font-semibold">
                        当前告警的动作审批
                      </h3>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary">
                        {scopedApprovalRequests.length} 条
                      </Badge>
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
                          ) : scopedApprovalRequests.length === 0 ? (
                            <div className="text-muted-foreground flex h-24 items-center justify-center text-sm">
                              当前告警没有待审批动作
                            </div>
                          ) : (
                            <div className="space-y-2">
                              {scopedApprovalRequests.map((request) => {
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

                      <ApprovalProposalSummary
                        request={activeApprovalRequest}
                      />

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
              </ReviewDetailGroup>

              <ReviewDetailGroup
                icon={CircleIcon}
                title="技术审计"
                description="查看运行版本、相似告警和完整结构化产物；日常做结论不必逐项阅读。"
              >
                <section className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                  <div className="rounded-md border">
                    <div className="flex items-center gap-2 border-b p-4">
                      <CircleIcon className="text-muted-foreground size-4" />
                      <h3 className="text-sm font-semibold">运行上下文</h3>
                    </div>
                    <dl className="p-4">
                      <DetailRow
                        label="状态"
                        value={
                          context?.run.status ?? fallbackSelectedItem.status
                        }
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
                      <h3 className="text-sm font-semibold">待办技术信息</h3>
                    </div>
                    <dl className="p-4">
                      <DetailRow
                        label="Queue ID"
                        value={fallbackSelectedItem.queue_id}
                      />
                      <DetailRow
                        label="原始原因码"
                        value={
                          reviewReasonCodes(fallbackSelectedItem).join(", ") ||
                          "-"
                        }
                      />
                      <DetailRow
                        label="Tenant"
                        value={fallbackSelectedItem.tenant_id ?? "-"}
                      />
                      <DetailRow
                        label="实体键"
                        value={
                          fallbackSelectedItem.entity_keys.length > 0
                            ? fallbackSelectedItem.entity_keys.join(", ")
                            : "-"
                        }
                      />
                    </dl>
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
                          normalization_report:
                            context?.run.normalization_report,
                          extraction_report: context?.run.extraction_report,
                          fact_reconstruction: context?.run.fact_reconstruction,
                          decision: context?.run.decision,
                        })}
                      </pre>
                    </div>
                  </div>
                </section>
              </ReviewDetailGroup>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
