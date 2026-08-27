"use client";

import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  BanIcon,
  BookOpenCheckIcon,
  FilePenLineIcon,
  LoaderCircleIcon,
  ShieldAlertIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { SocMemoryDecisionCapability } from "@/components/workspace/soc/soc-memory-decision-capability";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useCreateSocMemoryRevisionCandidate,
  useSocMemoryRecord,
} from "@/core/soc";
import type { SocMemoryRevisionIssueType } from "@/core/soc";

const ISSUE_OPTIONS: {
  value: SocMemoryRevisionIssueType;
  label: string;
  description: string;
  icon: typeof ShieldAlertIcon;
}[] = [
  {
    value: "incorrect_conclusion",
    label: "结论错误",
    description: "这次告警证明旧经验的风险结论不再成立。",
    icon: ShieldAlertIcon,
  },
  {
    value: "applicability_too_broad",
    label: "范围过宽",
    description: "旧经验本身有用，但匹配条件覆盖了不应复用的告警。",
    icon: BanIcon,
  },
  {
    value: "lesson_incomplete",
    label: "经验不完整",
    description: "结论方向基本正确，但 Business Lesson 缺少关键事实或边界。",
    icon: BookOpenCheckIcon,
  },
];

function formatTime(value?: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function SocMemoryRevisionWorkbench({
  memoryId,
  sourceRunId,
}: {
  memoryId: string;
  sourceRunId: string | null;
}) {
  const router = useRouter();
  const { record, isLoading, error } = useSocMemoryRecord(memoryId);
  const revisionMutation = useCreateSocMemoryRevisionCandidate();
  const [issueType, setIssueType] = useState<SocMemoryRevisionIssueType>(
    "incorrect_conclusion",
  );
  const [reason, setReason] = useState("");
  const selectedIssue = ISSUE_OPTIONS.find((item) => item.value === issueType);
  const canSubmit =
    record !== null &&
    reason.trim().length >= 10 &&
    !revisionMutation.isPending;

  const handleSubmit = async () => {
    if (!record || !canSubmit) return;
    try {
      const result = await revisionMutation.mutateAsync({
        memoryId: record.memory_id,
        request: {
          expected_record_version: record.version,
          ...(sourceRunId ? { source_run_id: sourceRunId } : {}),
          issue_type: issueType,
          reason: reason.trim(),
        },
      });
      toast.success("旧经验已暂停用于新告警，修订候选已创建");
      router.push(
        `/workspace/soc/review/memory-candidates/${result.candidate.candidate_id}`,
      );
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "创建修订候选失败");
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={FilePenLineIcon}
        title="纠正经验"
        description="暂停错误经验，创建可审核的新版本"
        actions={
          <Button size="sm" variant="outline" asChild>
            <Link
              href={
                sourceRunId
                  ? "/workspace/soc/corpus-validation"
                  : `/workspace/soc/memory/records/${encodeURIComponent(memoryId)}`
              }
            >
              <ArrowLeftIcon className="size-4" />
              {sourceRunId ? "返回告警演练" : "返回经验详情"}
            </Link>
          </Button>
        }
      />

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-5 py-6 md:px-7">
          <Alert className="rounded-md border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
            <AlertTriangleIcon />
            <AlertTitle>提交纠错会立即暂停旧经验用于新告警</AlertTitle>
            <AlertDescription>
              本次告警结论和历史记录不会被改写。新经验只有在候选审核通过后才会替代旧版本；旧版本会作为审计历史保留。
            </AlertDescription>
          </Alert>

          {isLoading ? (
            <div className="mt-5 space-y-4">
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-48 w-full" />
            </div>
          ) : error || !record ? (
            <Alert variant="destructive" className="mt-5 rounded-md">
              <AlertTriangleIcon />
              <AlertTitle>无法加载经验</AlertTitle>
              <AlertDescription>
                {error instanceof Error ? error.message : `未找到 ${memoryId}`}
              </AlertDescription>
            </Alert>
          ) : (
            <div className="mt-5 space-y-5">
              <section className="border">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-zinc-50 px-4 py-3 dark:bg-zinc-950/40">
                  <div>
                    <h2 className="text-sm font-semibold">当前经验</h2>
                    <p className="text-muted-foreground mt-1 font-mono text-xs">
                      {record.memory_id} · v{record.version}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">{record.status}</Badge>
                    <Badge
                      variant={
                        record.retrieval_enabled ? "default" : "secondary"
                      }
                    >
                      {record.retrieval_enabled
                        ? "已开放给新告警"
                        : "暂停用于新告警"}
                    </Badge>
                  </div>
                </div>
                <div className="grid gap-4 px-4 py-4 md:grid-cols-[minmax(0,1fr)_16rem]">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold break-words">
                      {record.summary}
                    </div>
                    <p className="text-muted-foreground mt-2 text-sm leading-6 whitespace-pre-wrap">
                      {record.business_lesson?.conclusion ?? record.content}
                    </p>
                  </div>
                  <dl className="grid content-start gap-2 border-t pt-4 text-xs md:border-t-0 md:border-l md:pt-0 md:pl-4">
                    <div>
                      <dt className="text-muted-foreground">触发纠错的运行</dt>
                      <dd className="mt-0.5 font-mono break-all">
                        {sourceRunId ?? "未提供"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">当前更新时间</dt>
                      <dd className="mt-0.5">
                        {formatTime(record.updated_at)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">匹配条件数量</dt>
                      <dd className="mt-0.5 tabular-nums">
                        {Object.keys(record.facets).length}
                      </dd>
                    </div>
                  </dl>
                </div>
              </section>

              <SocMemoryDecisionCapability record={record} />

              {!sourceRunId ? (
                <Alert className="rounded-md">
                  <BookOpenCheckIcon />
                  <AlertTitle>运营人员直接修订</AlertTitle>
                  <AlertDescription>
                    本次修订由经验台账直接发起，系统会使用旧经验
                    的来源与哈希作为审计依据。若选择“范围过宽”，仍需存在可回放的来源
                    Run。
                  </AlertDescription>
                </Alert>
              ) : null}

              <section className="border px-4 py-4">
                <h2 className="text-sm font-semibold">1. 选择发现的问题</h2>
                <ToggleGroup
                  type="single"
                  variant="outline"
                  value={issueType}
                  onValueChange={(value) => {
                    if (value)
                      setIssueType(value as SocMemoryRevisionIssueType);
                  }}
                  className="mt-3 grid w-full grid-cols-1 md:grid-cols-3"
                  aria-label="经验问题类型"
                >
                  {ISSUE_OPTIONS.map((item) => {
                    const Icon = item.icon;
                    return (
                      <ToggleGroupItem
                        key={item.value}
                        value={item.value}
                        className="h-11 w-full whitespace-normal"
                      >
                        <Icon className="size-4" />
                        {item.label}
                      </ToggleGroupItem>
                    );
                  })}
                </ToggleGroup>
                <p className="text-muted-foreground mt-2 text-sm">
                  {selectedIssue?.description}
                </p>

                <label className="mt-5 grid gap-2 text-sm font-medium">
                  2. 说明本次发现的业务事实或反证
                  <Textarea
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="例如：本次告警出现了已确认的真实攻击行为，旧经验将其误判为内部服务调用，因此不能继续复用。"
                    rows={6}
                    maxLength={4000}
                    disabled={revisionMutation.isPending}
                  />
                </label>
                <div className="text-muted-foreground mt-1 text-right text-xs tabular-nums">
                  {reason.trim().length}/4000，至少 10 字
                </div>

                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
                  <p className="text-muted-foreground max-w-2xl text-xs leading-5">
                    下一步会进入现有候选审核流程，可重新生成或编辑 Business
                    Lesson、收窄匹配范围，并决定新版本是否用于未来告警。
                  </p>
                  <Button
                    type="button"
                    disabled={!canSubmit}
                    onClick={handleSubmit}
                  >
                    {revisionMutation.isPending ? (
                      <LoaderCircleIcon className="size-4 animate-spin" />
                    ) : (
                      <FilePenLineIcon className="size-4" />
                    )}
                    暂停旧经验并创建修订候选
                  </Button>
                </div>
              </section>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
