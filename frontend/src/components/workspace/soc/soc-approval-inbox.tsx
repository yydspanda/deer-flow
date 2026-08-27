"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ClockIcon,
  FileJsonIcon,
  FlaskConicalIcon,
  KeyRoundIcon,
  PlayCircleIcon,
  RefreshCwIcon,
  ShieldXIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import {
  useCreateSocApprovalGrant,
  useDryRunSocApprovedAction,
  useExecuteSocApprovedAction,
  useExpireSocApprovalRequest,
  useRejectSocApprovalRequest,
  useSocApprovalRequest,
  useSocApprovalRequests,
} from "@/core/soc";
import type {
  SocAgentActionResult,
  SocAgentApprovalGrant,
  SocAgentApprovalRequest,
} from "@/core/soc";
import { cn } from "@/lib/utils";

function formatTime(value?: string | null) {
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

function requestTitle(request: SocAgentApprovalRequest) {
  return `${request.route} / ${request.action}`;
}

function riskCopy(value: string) {
  if (value === "high_risk") return "高风险";
  if (value === "analyst_write") return "运营写入";
  if (value === "read_only") return "只读";
  return "未知风险";
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 border-b py-2 text-sm last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

export function SocApprovalInbox() {
  const [status, setStatus] = useState<"pending" | "all">("pending");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [expiresInSeconds, setExpiresInSeconds] = useState("900");
  const [payloadJson, setPayloadJson] = useState("{}");
  const [grant, setGrant] = useState<SocAgentApprovalGrant | null>(null);
  const [executionResult, setExecutionResult] =
    useState<SocAgentActionResult | null>(null);

  const inbox = useSocApprovalRequests({
    status: status === "pending" ? "pending" : null,
    limit: 100,
  });
  const requests = useMemo(() => inbox.requests, [inbox.requests]);
  const fallback =
    requests.find((item) => item.approval_request_id === selectedId) ??
    requests[0] ??
    null;
  const activeId = fallback?.approval_request_id ?? null;
  const detail = useSocApprovalRequest(activeId);
  const active = detail.request ?? fallback;
  const createGrant = useCreateSocApprovalGrant();
  const rejectRequest = useRejectSocApprovalRequest();
  const expireRequest = useExpireSocApprovalRequest();
  const dryRunAction = useDryRunSocApprovedAction();
  const executeAction = useExecuteSocApprovedAction();

  useEffect(() => {
    if (!selectedId && requests[0]?.approval_request_id) {
      setSelectedId(requests[0].approval_request_id);
      return;
    }
    if (
      selectedId &&
      !requests.some((item) => item.approval_request_id === selectedId)
    ) {
      setSelectedId(requests[0]?.approval_request_id ?? null);
    }
  }, [requests, selectedId]);

  useEffect(() => {
    setGrant(null);
    setExecutionResult(null);
    setPayloadJson(JSON.stringify(active?.action_payload ?? {}, null, 2));
  }, [active?.action_payload, active?.approval_request_id]);

  const approve = async () => {
    if (!active?.approval_request_id || !reason.trim()) return;
    try {
      const created = await createGrant.mutateAsync({
        approval_request_id: active.approval_request_id,
        reason: reason.trim(),
        expires_in_seconds: Math.max(60, Number(expiresInSeconds) || 900),
      });
      setGrant(created);
      toast.success("动作已批准，生成一次性执行凭证");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "审批失败");
    }
  };

  const resolve = async (action: "reject" | "expire") => {
    if (!active?.approval_request_id || !reason.trim()) return;
    try {
      const command = {
        approvalRequestId: active.approval_request_id,
        request: { reason: reason.trim() },
      };
      if (action === "reject") {
        await rejectRequest.mutateAsync(command);
        toast.success("动作请求已驳回");
      } else {
        await expireRequest.mutateAsync(command);
        toast.success("动作请求已标记过期");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新失败");
    }
  };

  const execute = async (dryRun: boolean) => {
    if (!grant) return;
    let payload: Record<string, unknown>;
    try {
      const parsed = JSON.parse(payloadJson) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("payload 必须是 JSON object");
      }
      payload = parsed as Record<string, unknown>;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "payload JSON 无效");
      return;
    }
    try {
      const mutation = dryRun ? dryRunAction : executeAction;
      const result = await mutation.mutateAsync({
        execution_token_id: grant.execution_token_id,
        route: grant.route,
        action: grant.action,
        dry_run: dryRun,
        payload,
      });
      setExecutionResult(result);
      toast.success(dryRun ? "Dry-run 已完成" : "动作执行已完成");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "动作执行失败");
    }
  };

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={KeyRoundIcon}
        title="动作审批"
        description="封禁、隔离等高风险外部动作在这里独立授权，不改变告警研判结论"
        actions={
          <>
            <Select
              value={status}
              onValueChange={(value) => setStatus(value as "pending" | "all")}
            >
              <SelectTrigger className="w-32" aria-label="审批状态">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pending">待审批</SelectItem>
                <SelectItem value="all">全部请求</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="icon"
              aria-label="刷新动作审批"
              title="刷新动作审批"
              onClick={() => void inbox.refetch()}
              disabled={inbox.isFetching}
            >
              <RefreshCwIcon
                className={cn("size-4", inbox.isFetching && "animate-spin")}
              />
            </Button>
          </>
        }
      />

      <div className="grid min-h-0 flex-1 lg:grid-cols-[22rem_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-y-auto border-b p-2 lg:border-r lg:border-b-0">
          {inbox.isLoading ? (
            <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
              正在加载审批请求...
            </div>
          ) : inbox.error ? (
            <div className="text-destructive p-4 text-center text-sm">
              {inbox.error instanceof Error ? inbox.error.message : "加载失败"}
            </div>
          ) : requests.length === 0 ? (
            <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
              当前没有{status === "pending" ? "待审批" : ""}动作
            </div>
          ) : (
            <div className="space-y-1">
              {requests.map((request) => {
                const id = request.approval_request_id;
                const selected = id === activeId;
                return (
                  <button
                    key={id ?? request.permission_decision_id}
                    type="button"
                    onClick={() => setSelectedId(id ?? null)}
                    className={cn(
                      "hover:bg-accent w-full border-l-2 border-transparent px-3 py-3 text-left",
                      selected && "bg-accent border-l-foreground",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 truncate text-sm font-medium">
                        {requestTitle(request)}
                      </div>
                      <Badge variant="outline" className="shrink-0">
                        {riskCopy(request.risk_level)}
                      </Badge>
                    </div>
                    <p className="text-muted-foreground mt-2 line-clamp-2 text-xs leading-5">
                      {request.reason}
                    </p>
                    <div className="text-muted-foreground mt-2 flex justify-between text-xs">
                      <span>{request.status}</span>
                      <span>{formatTime(request.created_at)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </aside>

        <main className="min-h-0 overflow-y-auto">
          {!active ? (
            <div className="text-muted-foreground flex min-h-96 items-center justify-center text-sm">
              选择一个动作请求查看审批边界
            </div>
          ) : (
            <div className="mx-auto flex max-w-6xl flex-col gap-5 p-5 md:p-7">
              <section className="border">
                <div className="flex flex-wrap items-start justify-between gap-4 border-b p-5">
                  <div>
                    <h2 className="text-lg font-semibold">
                      {requestTitle(active)}
                    </h2>
                    <p className="text-muted-foreground mt-1 font-mono text-xs">
                      {active.approval_request_id ??
                        active.permission_decision_id}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Badge variant="outline">
                      {riskCopy(active.risk_level)}
                    </Badge>
                    <Badge variant="secondary">{active.status}</Badge>
                  </div>
                </div>
                <div className="grid gap-6 p-5 lg:grid-cols-[1.2fr_0.8fr]">
                  <div>
                    <div className="text-muted-foreground text-xs font-medium">
                      为什么申请执行
                    </div>
                    <p className="mt-3 text-sm leading-7">{active.reason}</p>
                  </div>
                  <dl>
                    <Field
                      label="申请人"
                      value={active.requested_by.actor_id}
                    />
                    <Field
                      label="来源建议"
                      value={active.source_proposal_id ?? "-"}
                    />
                    <Field
                      label="创建时间"
                      value={formatTime(active.created_at)}
                    />
                    <Field label="当前状态" value={active.status} />
                  </dl>
                </div>
              </section>

              {active.status === "pending" ? (
                <section className="border p-5">
                  <div className="flex items-center gap-2 text-sm font-semibold">
                    <KeyRoundIcon className="text-muted-foreground size-4" />
                    审批决定
                  </div>
                  <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_9rem]">
                    <Textarea
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                      placeholder="填写批准、驳回或过期的依据"
                      className="min-h-24 resize-none"
                    />
                    <div>
                      <label
                        className="text-sm font-medium"
                        htmlFor="approval-expiry"
                      >
                        授权有效期（秒）
                      </label>
                      <Input
                        id="approval-expiry"
                        value={expiresInSeconds}
                        onChange={(event) =>
                          setExpiresInSeconds(event.target.value)
                        }
                        inputMode="numeric"
                        className="mt-2"
                      />
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button
                      onClick={() => void approve()}
                      disabled={createGrant.isPending || !reason.trim()}
                    >
                      <CheckCircle2Icon className="size-4" />
                      批准一次执行
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => void resolve("reject")}
                      disabled={rejectRequest.isPending || !reason.trim()}
                    >
                      <ShieldXIcon className="size-4" />
                      驳回
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => void resolve("expire")}
                      disabled={expireRequest.isPending || !reason.trim()}
                    >
                      <ClockIcon className="size-4" />
                      标记过期
                    </Button>
                  </div>
                </section>
              ) : (
                <section className="flex items-start gap-3 border p-5">
                  <AlertTriangleIcon className="text-muted-foreground mt-0.5 size-5" />
                  <div>
                    <div className="text-sm font-semibold">
                      该请求已经完成审批
                    </div>
                    <p className="text-muted-foreground mt-1 text-sm">
                      {active.resolution_reason ?? "未记录补充说明"}
                    </p>
                  </div>
                </section>
              )}

              {grant ? (
                <section className="border">
                  <div className="flex items-center gap-2 border-b p-4 text-sm font-semibold">
                    <PlayCircleIcon className="text-muted-foreground size-4" />
                    已批准动作验证与执行
                  </div>
                  <div className="space-y-4 p-5">
                    <Textarea
                      value={payloadJson}
                      onChange={(event) => setPayloadJson(event.target.value)}
                      className="min-h-40 resize-none font-mono text-xs"
                      aria-label="动作 payload JSON"
                    />
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        onClick={() => void execute(true)}
                        disabled={dryRunAction.isPending}
                      >
                        <FlaskConicalIcon className="size-4" />
                        Dry-run
                      </Button>
                      <Button
                        onClick={() => void execute(false)}
                        disabled={
                          executeAction.isPending || grant.status !== "approved"
                        }
                      >
                        <PlayCircleIcon className="size-4" />
                        执行
                      </Button>
                    </div>
                    {executionResult ? (
                      <pre className="bg-muted max-h-72 overflow-auto p-4 text-xs whitespace-pre-wrap">
                        {JSON.stringify(executionResult, null, 2)}
                      </pre>
                    ) : null}
                  </div>
                </section>
              ) : null}

              <section className="border">
                <div className="flex items-center gap-2 border-b p-4 text-sm font-semibold">
                  <FileJsonIcon className="text-muted-foreground size-4" />
                  请求载荷
                </div>
                <pre className="bg-muted/40 max-h-80 overflow-auto p-4 text-xs whitespace-pre-wrap">
                  {JSON.stringify(active.action_payload ?? {}, null, 2)}
                </pre>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
