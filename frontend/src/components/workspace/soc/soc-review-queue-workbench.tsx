"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CircleIcon,
  FlaskConicalIcon,
  KeyRoundIcon,
  PlayCircleIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
  XCircleIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  useCloseSocReviewItem,
  useCorrectSocReviewRun,
  useCreateSocApprovalGrant,
  useDryRunSocApprovedAction,
  useExecuteSocApprovedAction,
  useSocReviewContext,
  useSocReviewItems,
} from "@/core/soc";
import type {
  SocAgentActionResult,
  SocAgentApprovalGrant,
  SocAgentApprovalRequest,
  SocAgentApprovedActionCommand,
  SocReviewQueueItem,
  SocReviewQueueStatus,
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

export function SocReviewQueueWorkbench() {
  const [statusFilter, setStatusFilter] = useState<
    SocReviewQueueStatus | "all"
  >("open");
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(null);
  const [closeReason, setCloseReason] = useState("复核完成");
  const [correctionReason, setCorrectionReason] = useState("");
  const [correctedVerdict, setCorrectedVerdict] =
    useState<SocVerdict>("false_positive");
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

  const status = statusFilter === "all" ? null : statusFilter;
  const { items, isLoading, isFetching, error, refetch } = useSocReviewItems({
    status,
    limit: 50,
  });
  const selectedItem = useMemo(
    () => items.find((item) => item.queue_id === selectedQueueId) ?? null,
    [items, selectedQueueId],
  );
  const fallbackSelectedItem = selectedItem ?? items[0] ?? null;
  const activeQueueId =
    selectedItem?.queue_id ?? fallbackSelectedItem?.queue_id;
  const { context, isLoading: contextLoading } =
    useSocReviewContext(activeQueueId);
  const closeMutation = useCloseSocReviewItem();
  const correctMutation = useCorrectSocReviewRun();
  const createApprovalGrantMutation = useCreateSocApprovalGrant();
  const dryRunApprovedActionMutation = useDryRunSocApprovedAction();
  const executeApprovedActionMutation = useExecuteSocApprovedAction();

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
      const approvalRequest =
        parseJsonObject<SocAgentApprovalRequest>(approvalRequestJson);
      const grant = await createApprovalGrantMutation.mutateAsync({
        approval_request: approvalRequest,
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

  return (
    <div className="flex size-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">SOC 复核</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            人工复核告警研判结果，沉淀纠正信号。
          </p>
        </div>
        <div className="flex items-center gap-2">
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
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[24rem_minmax(0,1fr)]">
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
                    onClick={() => setSelectedQueueId(item.queue_id)}
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
                  <div className="text-muted-foreground text-right text-xs">
                    <div>
                      更新 {formatTime(fallbackSelectedItem.updated_at)}
                    </div>
                    <div>
                      创建 {formatTime(fallbackSelectedItem.created_at)}
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

              <section className="rounded-md border">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
                  <div className="flex items-center gap-2">
                    <KeyRoundIcon className="text-muted-foreground size-4" />
                    <h3 className="text-sm font-semibold">审批动作</h3>
                  </div>
                  {approvalGrant ? (
                    <Badge variant="outline">{approvalGrant.status}</Badge>
                  ) : null}
                </div>
                <div className="grid grid-cols-1 gap-5 p-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <label
                        className="text-sm font-medium"
                        htmlFor="approval-request-json"
                      >
                        审批请求 JSON
                      </label>
                      <Textarea
                        id="approval-request-json"
                        value={approvalRequestJson}
                        onChange={(event) =>
                          setApprovalRequestJson(event.target.value)
                        }
                        className="min-h-52 resize-none font-mono text-xs"
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
                    <Button
                      size="sm"
                      onClick={() => void handleCreateApprovalGrant()}
                      disabled={
                        createApprovalGrantMutation.isPending ||
                        approvalReason.trim().length === 0
                      }
                    >
                      <KeyRoundIcon className="size-4" />
                      生成审批 token
                    </Button>
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
