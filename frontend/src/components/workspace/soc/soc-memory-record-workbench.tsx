"use client";

import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  CheckCircle2Icon,
  DatabaseIcon,
  FilePenLineIcon,
  FlaskConicalIcon,
  HistoryIcon,
  LoaderCircleIcon,
  PowerIcon,
  SearchCheckIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useSocMemoryLineage,
  useTestSocMemoryRecordMatch,
  useUpdateSocMemoryRetrievalActivation,
} from "@/core/soc";
import type {
  SocMemoryBusinessLesson,
  SocMemoryRecord,
  SocMemoryUseEffect,
} from "@/core/soc";

const USE_EFFECT_LABELS: Record<SocMemoryUseEffect, string> = {
  context_only: "仅作分析背景",
  reinforced: "支持原结论",
  overridden: "改变最终结论",
  conflicted: "与当前证据冲突",
};

const EXCLUSION_LABELS: Record<string, string> = {
  retrieval_disabled: "该 Memory 当前未启用召回",
  activation_not_governed: "召回缺少完整治理配置",
  activation_expired: "召回有效期已结束",
  review_overdue: "定期复核已逾期",
  record_status_or_validity: "记录状态或经验有效期不允许召回",
  missing_strong_anchor: "没有命中足够的强匹配条件",
  not_applicable: "适用范围检查未通过",
  below_minimum_score: "相关性分数不足",
};

function formatTime(value?: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function futureLocalDate(days: number) {
  const value = new Date(Date.now() + days * 24 * 60 * 60 * 1000);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function LessonSection({ lesson }: { lesson: SocMemoryBusinessLesson }) {
  const groups = [
    ["业务依据", lesson.business_rationale],
    ["适用条件", lesson.applicability_conditions],
    ["泛化边界", lesson.generalization_boundaries],
    ["失效条件", lesson.invalidation_conditions],
    ["处理建议", lesson.handling_guidance],
  ] as const;
  return (
    <div className="space-y-4">
      <div>
        <div className="text-muted-foreground text-xs">核心结论</div>
        <p className="mt-1 text-sm leading-6">{lesson.conclusion}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {groups.map(([label, values]) => (
          <div key={label} className="border-t pt-3">
            <div className="text-xs font-semibold">{label}</div>
            <ul className="text-muted-foreground mt-2 space-y-1 text-sm leading-6">
              {values.map((value) => (
                <li key={value}>• {value}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function FacetSection({ record }: { record: SocMemoryRecord }) {
  const required = record.applicability?.required_facets ?? {};
  const optional = record.applicability?.optional_facets ?? {};
  const rows = [
    ...Object.entries(required).map(([key, values]) => ({
      key,
      values,
      kind: "必须命中",
    })),
    ...Object.entries(optional).map(([key, values]) => ({
      key,
      values,
      kind: "可选收窄",
    })),
  ];
  return rows.length === 0 ? (
    <p className="text-muted-foreground text-sm">
      这条历史 Memory 没有结构化适用范围，只能作为非决策背景使用。
    </p>
  ) : (
    <div className="divide-y border">
      {rows.map((row) => (
        <div
          key={`${row.kind}:${row.key}`}
          className="grid gap-2 px-3 py-3 text-sm md:grid-cols-[7rem_12rem_minmax(0,1fr)]"
        >
          <Badge variant={row.kind === "必须命中" ? "default" : "outline"}>
            {row.kind}
          </Badge>
          <span className="font-mono text-xs">{row.key}</span>
          <div className="flex flex-wrap gap-1.5">
            {row.values.map((value) => (
              <Badge key={value} variant="secondary" className="max-w-full">
                <span className="truncate" title={value}>
                  {value}
                </span>
              </Badge>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function SocMemoryRecordWorkbench({ memoryId }: { memoryId: string }) {
  const { lineage, isLoading, error } = useSocMemoryLineage(memoryId);
  const retrievalMutation = useUpdateSocMemoryRetrievalActivation();
  const matchMutation = useTestSocMemoryRecordMatch();
  const [governanceReason, setGovernanceReason] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [reviewAfterDays, setReviewAfterDays] = useState("30");
  const [locatorType, setLocatorType] = useState<"alert" | "run">("alert");
  const [locator, setLocator] = useState("");
  const record = lineage?.record ?? null;
  const uses = lineage?.uses ?? [];

  useEffect(() => {
    if (!validUntil) setValidUntil(futureLocalDate(60));
  }, [validUntil]);

  const handleRetrieval = async () => {
    if (!record || !governanceReason.trim()) return;
    const action = record.retrieval_enabled ? "disable" : "enable";
    try {
      await retrievalMutation.mutateAsync({
        memoryId: record.memory_id,
        request: {
          action,
          expected_record_version: record.version,
          reason: governanceReason.trim(),
          ...(action === "enable"
            ? {
                activation_valid_until: new Date(validUntil).toISOString(),
                review_after_days: Number(reviewAfterDays),
              }
            : {}),
        },
      });
      setGovernanceReason("");
      toast.success(action === "enable" ? "召回已启用" : "召回已停用");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "召回状态更新失败");
    }
  };

  const handleMatchTest = async () => {
    if (!record || !locator.trim()) return;
    try {
      await matchMutation.mutateAsync({
        memoryId: record.memory_id,
        request:
          locatorType === "alert"
            ? { alert_id: locator.trim() }
            : { run_id: locator.trim() },
      });
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "匹配试算失败");
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={DatabaseIcon}
        title="Memory 详情"
        description="查看经验、使用历史和版本化治理状态"
        actions={
          <Button size="sm" variant="outline" asChild>
            <Link href="/workspace/soc/memory/records">
              <ArrowLeftIcon className="size-4" />
              返回 Memory 台账
            </Link>
          </Button>
        }
      />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl space-y-5 p-4 md:p-6">
          {isLoading ? (
            <>
              <Skeleton className="h-40 w-full" />
              <Skeleton className="h-72 w-full" />
            </>
          ) : error || !record ? (
            <Alert variant="destructive">
              <AlertTriangleIcon />
              <AlertTitle>无法加载 Memory</AlertTitle>
              <AlertDescription>
                {error instanceof Error ? error.message : `未找到 ${memoryId}`}
              </AlertDescription>
            </Alert>
          ) : (
            <>
              <section className="border">
                <div className="flex flex-wrap items-start justify-between gap-4 border-b bg-zinc-50 px-5 py-4 dark:bg-zinc-950/40">
                  <div className="min-w-0">
                    <div className="font-mono text-xs">
                      {record.memory_id} · v{record.version}
                    </div>
                    <h1 className="mt-2 text-lg font-semibold break-words">
                      {record.summary}
                    </h1>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">{record.status}</Badge>
                    <Badge
                      variant={
                        record.retrieval_enabled ? "default" : "secondary"
                      }
                    >
                      {record.retrieval_enabled ? "召回已启用" : "召回未启用"}
                    </Badge>
                    <Badge variant="outline">{record.memory_type}</Badge>
                  </div>
                </div>
                <dl className="grid text-sm sm:grid-cols-2 lg:grid-cols-4">
                  <div className="border-b px-4 py-3 lg:border-r">
                    <dt className="text-muted-foreground text-xs">租户范围</dt>
                    <dd className="mt-1">
                      {record.tenant_id ?? record.tenant_scope}
                    </dd>
                  </div>
                  <div className="border-b px-4 py-3 lg:border-r">
                    <dt className="text-muted-foreground text-xs">Profile</dt>
                    <dd className="mt-1">
                      {record.applicability
                        ? `${record.applicability.profile_id} v${record.applicability.profile_version}`
                        : "-"}
                    </dd>
                  </div>
                  <div className="border-b px-4 py-3 lg:border-r">
                    <dt className="text-muted-foreground text-xs">
                      来源告警 / Run
                    </dt>
                    <dd className="mt-1 font-mono text-xs break-all">
                      {record.source.alert_id ?? "-"} /{" "}
                      {record.source.run_id ?? "-"}
                    </dd>
                  </div>
                  <div className="border-b px-4 py-3">
                    <dt className="text-muted-foreground text-xs">更新时间</dt>
                    <dd className="mt-1">{formatTime(record.updated_at)}</dd>
                  </div>
                </dl>
              </section>

              {record.metadata.revision_pending === true ? (
                <Alert className="border-amber-300 bg-amber-50 text-amber-950">
                  <AlertTriangleIcon />
                  <AlertTitle>该 Memory 正在修订</AlertTitle>
                  <AlertDescription>
                    旧版本已暂停召回，请先完成现有修订 Candidate 的审核。
                  </AlertDescription>
                </Alert>
              ) : null}

              <section className="border px-5 py-5">
                <h2 className="text-sm font-semibold">
                  Business Lesson / 业务经验
                </h2>
                <div className="mt-4">
                  {record.business_lesson ? (
                    <LessonSection lesson={record.business_lesson} />
                  ) : (
                    <p className="text-muted-foreground text-sm leading-6 whitespace-pre-wrap">
                      {record.content}
                    </p>
                  )}
                </div>
              </section>

              <section className="border px-5 py-5">
                <h2 className="text-sm font-semibold">
                  Applicability / 匹配范围
                </h2>
                <p className="text-muted-foreground mt-1 text-xs leading-5">
                  必须条件决定能否精确复用；可选条件只用于收窄和排序，不能单独授权改判。
                </p>
                <div className="mt-4">
                  <FacetSection record={record} />
                </div>
              </section>

              <section className="border px-5 py-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold">
                      Governance / 版本化治理
                    </h2>
                    <p className="text-muted-foreground mt-1 text-xs">
                      不原地改写已确认经验；修订会暂停旧版本并创建待审
                      Candidate。
                    </p>
                  </div>
                  <Button
                    size="sm"
                    disabled={record.metadata.revision_pending === true}
                    asChild
                  >
                    <Link
                      href={`/workspace/soc/memory/records/${encodeURIComponent(record.memory_id)}/revise`}
                    >
                      <FilePenLineIcon className="size-4" />
                      创建修订版本
                    </Link>
                  </Button>
                </div>
                {record.status === "confirmed" ? (
                  <div className="mt-4 grid gap-3 border-t pt-4 lg:grid-cols-[minmax(0,1fr)_13rem_8rem_auto] lg:items-end">
                    <label className="grid gap-1 text-xs">
                      <span className="text-muted-foreground">
                        召回治理理由
                      </span>
                      <Input
                        value={governanceReason}
                        onChange={(event) =>
                          setGovernanceReason(event.target.value)
                        }
                        placeholder="说明为什么启用或停用"
                      />
                    </label>
                    <label className="grid gap-1 text-xs">
                      <span className="text-muted-foreground">召回有效至</span>
                      <Input
                        type="datetime-local"
                        value={validUntil}
                        onChange={(event) => setValidUntil(event.target.value)}
                        disabled={record.retrieval_enabled}
                      />
                    </label>
                    <label className="grid gap-1 text-xs">
                      <span className="text-muted-foreground">复核天数</span>
                      <Input
                        type="number"
                        min={1}
                        max={365}
                        value={reviewAfterDays}
                        onChange={(event) =>
                          setReviewAfterDays(event.target.value)
                        }
                        disabled={record.retrieval_enabled}
                      />
                    </label>
                    <Button
                      size="sm"
                      variant={record.retrieval_enabled ? "outline" : "default"}
                      disabled={
                        !governanceReason.trim() || retrievalMutation.isPending
                      }
                      onClick={() => void handleRetrieval()}
                    >
                      {retrievalMutation.isPending ? (
                        <LoaderCircleIcon className="size-4 animate-spin" />
                      ) : (
                        <PowerIcon className="size-4" />
                      )}
                      {record.retrieval_enabled ? "停用召回" : "启用召回"}
                    </Button>
                  </div>
                ) : null}
              </section>

              <section className="border px-5 py-5">
                <div className="flex items-center gap-2">
                  <FlaskConicalIcon className="size-4" />
                  <h2 className="text-sm font-semibold">
                    Match Test / 匹配试算
                  </h2>
                </div>
                <p className="text-muted-foreground mt-1 text-xs leading-5">
                  使用已保存的 Runtime 输入执行生产同款召回门禁；不调用
                  LLM、不写数据库、不改变结论。
                </p>
                <div className="mt-4 flex flex-wrap items-end gap-3">
                  <ToggleGroup
                    type="single"
                    variant="outline"
                    value={locatorType}
                    onValueChange={(value) => {
                      if (value) setLocatorType(value as "alert" | "run");
                    }}
                    aria-label="匹配试算输入类型"
                  >
                    <ToggleGroupItem value="alert">Alert ID</ToggleGroupItem>
                    <ToggleGroupItem value="run">Run ID</ToggleGroupItem>
                  </ToggleGroup>
                  <Input
                    className="min-w-64 flex-1"
                    value={locator}
                    onChange={(event) => setLocator(event.target.value)}
                    placeholder={
                      locatorType === "alert" ? "输入 Alert ID" : "输入 Run ID"
                    }
                  />
                  <Button
                    variant="outline"
                    disabled={!locator.trim() || matchMutation.isPending}
                    onClick={() => void handleMatchTest()}
                  >
                    {matchMutation.isPending ? (
                      <LoaderCircleIcon className="size-4 animate-spin" />
                    ) : (
                      <SearchCheckIcon className="size-4" />
                    )}
                    执行试算
                  </Button>
                </div>
                {matchMutation.data ? (
                  <div className="mt-4 border-l-4 border-l-zinc-500 bg-zinc-50 px-4 py-3 text-sm dark:bg-zinc-950/40">
                    <div className="flex flex-wrap items-center gap-2">
                      {matchMutation.data.matched ? (
                        <CheckCircle2Icon className="size-4 text-emerald-700" />
                      ) : (
                        <AlertTriangleIcon className="size-4 text-amber-700" />
                      )}
                      <span className="font-semibold">
                        {matchMutation.data.matched
                          ? "会命中该 Memory"
                          : "不会命中该 Memory"}
                      </span>
                      <Badge variant="outline">
                        {matchMutation.data.profile_id} v
                        {matchMutation.data.profile_version}
                      </Badge>
                      {matchMutation.data.match ? (
                        <Badge variant="secondary">
                          score {matchMutation.data.match.score}
                        </Badge>
                      ) : null}
                    </div>
                    {matchMutation.data.exclusion_reasons.length > 0 ? (
                      <ul className="text-muted-foreground mt-2 space-y-1 text-xs">
                        {matchMutation.data.exclusion_reasons.map((reason) => (
                          <li key={reason}>
                            • {EXCLUSION_LABELS[reason] ?? reason}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {matchMutation.data.match?.match_reasons.length ? (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {matchMutation.data.match.match_reasons.map(
                          (reason) => (
                            <Badge key={reason} variant="outline">
                              {reason}
                            </Badge>
                          ),
                        )}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </section>

              <section className="border">
                <div className="flex items-center justify-between border-b px-5 py-3">
                  <h2 className="flex items-center gap-2 text-sm font-semibold">
                    <HistoryIcon className="size-4" />
                    Usage History / 使用历史
                  </h2>
                  <Badge variant="secondary">{uses.length}</Badge>
                </div>
                {uses.length === 0 ? (
                  <p className="text-muted-foreground px-5 py-8 text-center text-sm">
                    这条 Memory
                    尚未被后续告警召回。来源告警用于创建它，不计为一次使用。
                  </p>
                ) : (
                  <div className="divide-y">
                    {uses.map((use) => (
                      <div
                        key={use.use_id}
                        className="grid gap-3 px-5 py-3 text-sm md:grid-cols-[minmax(0,1fr)_10rem_9rem_10rem] md:items-center"
                      >
                        <div className="min-w-0">
                          <div className="font-medium">
                            Alert {use.alert_id}
                          </div>
                          <div className="text-muted-foreground mt-1 truncate font-mono text-xs">
                            {use.run_id} · {use.use_id}
                          </div>
                        </div>
                        <Badge variant="outline">
                          {USE_EFFECT_LABELS[use.effect]}
                        </Badge>
                        <div className="text-xs">
                          {use.base_verdict} → {use.effective_verdict}
                        </div>
                        <div className="text-muted-foreground text-xs md:text-right">
                          {formatTime(use.created_at)}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="border px-5 py-4 text-xs">
                <div className="font-semibold">Audit Lineage / 审计来源</div>
                <div className="text-muted-foreground mt-2 grid gap-1 font-mono break-all">
                  <span>Candidate: {record.source_candidate_id}</span>
                  <span>Content hash: {record.content_hash}</span>
                  <span>Facets hash: {record.facets_hash}</span>
                  {record.revision_lineage ? (
                    <span>
                      Revision: {record.revision_lineage.revision_origin} from{" "}
                      {record.revision_lineage.predecessor_memory_id}
                    </span>
                  ) : null}
                </div>
                <Button variant="link" size="sm" className="mt-2 px-0" asChild>
                  <Link
                    href={`/workspace/soc/review/memory-candidates/${encodeURIComponent(record.source_candidate_id)}`}
                  >
                    查看来源 Candidate 治理记录
                  </Link>
                </Button>
              </section>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
