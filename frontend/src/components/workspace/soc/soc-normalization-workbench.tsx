"use client";

import {
  AlertTriangleIcon,
  ActivityIcon,
  BanIcon,
  CheckCircle2Icon,
  EyeIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  WrenchIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  useSocNormalizationBaselines,
  useSocNormalizationIssues,
  useSocNormalizationMetrics,
  useUpdateSocNormalizationIssue,
} from "@/core/soc";
import type {
  SocNormalizationIssueStatus,
  SocNormalizationMaintenanceIssue,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const STATUS_OPTIONS: {
  value: SocNormalizationIssueStatus | "all";
  label: string;
}[] = [
  { value: "open", label: "待处理" },
  { value: "acknowledged", label: "已确认" },
  { value: "resolved", label: "已解决" },
  { value: "ignored", label: "已忽略" },
  { value: "all", label: "全部" },
];

const ISSUE_LABELS: Record<string, string> = {
  baseline_missing: "缺少基线",
  novel_schema: "新 Schema",
  degraded_schema: "解析降级",
  unsupported_schema: "格式不支持",
  high_value_gap: "关键字段未映射",
  evidence_truncated: "证据被截断",
};

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function severityClass(issue: SocNormalizationMaintenanceIssue) {
  if (issue.severity === "critical") {
    return "border-red-300 bg-red-50 text-red-700";
  }
  if (issue.severity === "warning") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  return "border-sky-300 bg-sky-50 text-sky-700";
}

function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 border-b py-2 text-sm last:border-b-0">
      <div className="text-muted-foreground">{label}</div>
      <div className="min-w-0 font-mono text-xs break-words">
        {value?.trim() ? value : "-"}
      </div>
    </div>
  );
}

export function SocNormalizationWorkbench() {
  const [status, setStatus] = useState<SocNormalizationIssueStatus | "all">(
    "open",
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const issueQuery = useSocNormalizationIssues({
    status: status === "all" ? null : status,
  });
  const baselineQuery = useSocNormalizationBaselines();
  const metricsQuery = useSocNormalizationMetrics();
  const updateIssue = useUpdateSocNormalizationIssue();
  const issues = useMemo(() => issueQuery.issues, [issueQuery.issues]);
  const selected =
    issues.find((item) => item.issue_id === selectedId) ?? issues[0] ?? null;

  useEffect(() => {
    if (!selectedId && issues[0]) setSelectedId(issues[0].issue_id);
    if (selectedId && !issues.some((item) => item.issue_id === selectedId)) {
      setSelectedId(issues[0]?.issue_id ?? null);
    }
  }, [issues, selectedId]);

  const refresh = async () => {
    await Promise.all([
      issueQuery.refetch(),
      baselineQuery.refetch(),
      metricsQuery.refetch(),
    ]);
  };

  const update = async (
    nextStatus: Exclude<SocNormalizationIssueStatus, "open">,
  ) => {
    if (!selected || !reason.trim()) {
      toast.error("请填写处理理由");
      return;
    }
    try {
      await updateIssue.mutateAsync({
        issueId: selected.issue_id,
        request: { status: nextStatus, reason: reason.trim() },
      });
      setReason("");
      toast.success("维护问题已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新失败");
    }
  };

  const metrics = metricsQuery.metrics;
  return (
    <div className="flex size-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div className="flex items-center gap-3">
          <WrenchIcon className="size-5" />
          <div>
            <h1 className="text-xl font-semibold">归一化运维</h1>
            <p className="text-muted-foreground mt-0.5 text-sm">
              Normalization maintenance
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link href="/workspace/soc/operations">
              <ActivityIcon className="size-4" />
              运营观察
            </Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link href="/workspace/soc/review">
              <ShieldCheckIcon className="size-4" />
              告警复核
            </Link>
          </Button>
          <Select
            value={status}
            onValueChange={(value) =>
              setStatus(value as SocNormalizationIssueStatus | "all")
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
            size="icon-sm"
            onClick={() => void refresh()}
            disabled={issueQuery.isFetching}
            title="刷新"
          >
            <RefreshCwIcon
              className={cn("size-4", issueQuery.isFetching && "animate-spin")}
            />
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-2 border-b sm:grid-cols-4">
        <div className="border-r px-5 py-3">
          <div className="text-muted-foreground text-xs">Open issues</div>
          <div className="mt-1 text-lg font-semibold">
            {metrics?.open_issue_count ?? "-"}
          </div>
        </div>
        <div className="border-r px-5 py-3">
          <div className="text-muted-foreground text-xs">Critical</div>
          <div className="mt-1 text-lg font-semibold text-red-700">
            {metrics?.severity_counts.critical ?? 0}
          </div>
        </div>
        <div className="border-r px-5 py-3">
          <div className="text-muted-foreground text-xs">New schemas</div>
          <div className="mt-1 text-lg font-semibold">
            {metrics?.issue_type_counts.novel_schema ?? 0}
          </div>
        </div>
        <div className="px-5 py-3">
          <div className="text-muted-foreground text-xs">Active baselines</div>
          <div className="mt-1 text-lg font-semibold">
            {metrics?.active_baseline_count ?? baselineQuery.baselines.length}
          </div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[25rem_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-y-auto border-r">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <span className="text-sm font-medium">维护队列</span>
            <Badge variant="secondary">{issues.length}</Badge>
          </div>
          {issueQuery.isLoading ? (
            <div className="text-muted-foreground p-6 text-sm">加载中...</div>
          ) : issueQuery.error ? (
            <div className="p-6 text-sm text-red-700">
              {issueQuery.error instanceof Error
                ? issueQuery.error.message
                : "加载失败"}
            </div>
          ) : issues.length === 0 ? (
            <div className="text-muted-foreground p-6 text-sm">
              当前队列为空
            </div>
          ) : (
            <div className="divide-y">
              {issues.map((issue) => (
                <button
                  type="button"
                  key={issue.issue_id}
                  onClick={() => setSelectedId(issue.issue_id)}
                  className={cn(
                    "hover:bg-muted/60 w-full px-4 py-3 text-left transition-colors",
                    selected?.issue_id === issue.issue_id && "bg-muted",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">
                        {ISSUE_LABELS[issue.issue_type] ?? issue.issue_type}
                      </div>
                      <div className="text-muted-foreground mt-1 truncate text-xs">
                        {issue.source_system ?? "unknown"} / {issue.adapter}
                      </div>
                    </div>
                    <Badge variant="outline" className={severityClass(issue)}>
                      {issue.severity}
                    </Badge>
                  </div>
                  <div className="text-muted-foreground mt-2 flex justify-between text-xs">
                    <span>出现 {issue.occurrence_count} 次</span>
                    <span>{formatTime(issue.last_seen_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </aside>

        <main className="min-h-0 overflow-y-auto">
          {!selected ? (
            <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
              选择维护问题
            </div>
          ) : (
            <div className="mx-auto max-w-5xl px-6 py-5">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <AlertTriangleIcon className="size-4" />
                    <h2 className="text-base font-semibold">
                      {ISSUE_LABELS[selected.issue_type] ?? selected.issue_type}
                    </h2>
                  </div>
                  <div className="text-muted-foreground mt-1 font-mono text-xs">
                    {selected.issue_id}
                  </div>
                </div>
                <Badge variant="outline">{selected.status}</Badge>
              </div>

              <section className="py-4">
                <Field label="Source system" value={selected.source_system} />
                <Field label="Adapter" value={selected.adapter} />
                <Field label="Parser" value={selected.parser_name} />
                <Field label="Parser version" value={selected.parser_version} />
                <Field
                  label="Schema fingerprint"
                  value={selected.schema_fingerprint}
                />
                <Field label="Source path" value={selected.source_path} />
                <Field
                  label="Canonical target"
                  value={selected.expected_target}
                />
                <Field
                  label="Run / Alert"
                  value={`${selected.run_id ?? "-"} / ${selected.alert_id ?? "-"}`}
                />
              </section>

              {selected.status === "open" ||
              selected.status === "acknowledged" ? (
                <section className="border-t pt-4">
                  <label
                    htmlFor="normalization-resolution"
                    className="text-sm font-medium"
                  >
                    处理理由
                  </label>
                  <Textarea
                    id="normalization-resolution"
                    className="mt-2 min-h-24"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                  <div className="mt-3 flex flex-wrap gap-2">
                    {selected.status === "open" ? (
                      <Button
                        variant="outline"
                        onClick={() => void update("acknowledged")}
                        disabled={updateIssue.isPending}
                      >
                        <EyeIcon className="size-4" />
                        确认接手
                      </Button>
                    ) : null}
                    <Button
                      onClick={() => void update("resolved")}
                      disabled={updateIssue.isPending}
                    >
                      <CheckCircle2Icon className="size-4" />
                      标记解决
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => void update("ignored")}
                      disabled={updateIssue.isPending}
                    >
                      <BanIcon className="size-4" />
                      忽略
                    </Button>
                  </div>
                </section>
              ) : selected.resolution_reason ? (
                <section className="border-t pt-4 text-sm">
                  <div className="text-muted-foreground text-xs">
                    Resolution
                  </div>
                  <div className="mt-1">{selected.resolution_reason}</div>
                </section>
              ) : null}

              <section className="mt-6 border-t pt-4">
                <div className="text-sm font-medium">Active baselines</div>
                <div className="mt-2 divide-y border-y">
                  {baselineQuery.baselines.slice(0, 8).map((baseline) => (
                    <div
                      key={baseline.baseline_id}
                      className="grid gap-1 py-2 text-xs sm:grid-cols-[minmax(0,1fr)_8rem_5rem]"
                    >
                      <span className="truncate font-mono">
                        {baseline.parser_name}@{baseline.parser_version}
                      </span>
                      <span className="text-muted-foreground truncate">
                        {baseline.source_system ?? "global"}
                      </span>
                      <span className="text-right">
                        {baseline.accepted_fingerprints.length} fp
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
