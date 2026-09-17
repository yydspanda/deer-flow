"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  EyeIcon,
  ClipboardCheckIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  ClockIcon,
} from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  createSocCorpusRound,
  getSocCorpusExperimentConfiguration,
  getSocCorpusExperiments,
  getSocCorpusRound,
  getSocCorpusRoundResults,
  getSocCorpusRounds,
  pauseSocCorpusRound,
  prepareSocCorpusExperiment,
  retrySocCorpusRound,
  startSocCorpusRound,
} from "@/core/soc/api";
import {
  corpusRoundSelection,
  corpusRoundStateLabel,
  corpusDuration,
  type SocCorpusRoundResult,
} from "@/core/soc/corpus-experiments";
import type { SocCorpusBatch } from "@/core/soc/types";

import { SocCorpusRoundComparison } from "./soc-corpus-round-comparison";

const AuditViewer = dynamic(() =>
  import("./soc-corpus-audit-viewer").then(
    (module) => module.SocCorpusAuditViewer,
  ),
);
const QUERY = ["soc-corpus-experiments"] as const;
const JOB_LABELS: Record<string, string> = {
  queued: "待运行",
  claimed: "已受理",
  checking: "准备中",
  prechecking: "准备中",
  analyzing: "研判中",
  projecting: "保存结果中",
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
};
const handlingLabel = (value?: string | null) =>
  value === "ignore" ? "忽略" : value === "transfer" ? "转交" : "未形成结论";

export function SocCorpusExperiments({
  batch,
  tier,
  groupId,
  requestedAlert,
  onRequestHandled,
}: {
  batch: SocCorpusBatch;
  tier: "main" | "supplementary" | "all";
  groupId: string;
  requestedAlert: { alertId: string; key: number } | null;
  onRequestHandled: () => void;
}) {
  const cache = useQueryClient();
  const [experimentId, setExperimentId] = useState("");
  const [restored, setRestored] = useState(false);
  const [roundId, setRoundId] = useState("");
  const [roundOffset, setRoundOffset] = useState(0);
  const [experimentOffset, setExperimentOffset] = useState(0);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState(false);
  const [prepareOpen, setPrepareOpen] = useState(false);
  const [name, setName] = useState("");
  const [alertId, setAlertId] = useState("");
  const [limit, setLimit] = useState("5");
  const [concurrency, setConcurrency] = useState("3");
  const [purpose, setPurpose] = useState<"memory" | "full_flow">("memory");
  const [memoryMode, setMemoryMode] = useState<"snapshot" | "none">("snapshot");
  const [compareToCurrent, setCompareToCurrent] = useState(false);
  const [detail, setDetail] = useState<SocCorpusRoundResult | null>(null);
  const commandKey = useRef<{ body: string; key: string } | null>(null);
  const prepareId = useRef<string | null>(null);
  const controlsRound = useRef("");
  const experiments = useQuery({
    queryKey: [...QUERY, "list", experimentOffset],
    queryFn: () => getSocCorpusExperiments(experimentOffset),
    staleTime: 30_000,
    retry: false,
  });
  const configuration = useQuery({
    queryKey: [...QUERY, "configuration"],
    queryFn: getSocCorpusExperimentConfiguration,
    staleTime: 30_000,
    retry: false,
  });
  const rounds = useQuery({
    queryKey: [...QUERY, experimentId, "rounds", batch, roundOffset],
    queryFn: () => getSocCorpusRounds(experimentId, batch, roundOffset),
    enabled: !!experimentId,
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: false,
  });
  const progress = useQuery({
    queryKey: [...QUERY, "round", roundId],
    queryFn: () => getSocCorpusRound(roundId),
    enabled: !!roundId,
    refetchInterval: (query) =>
      query.state.data?.round.state === "running" ||
      query.state.data?.active_count
        ? 3_000
        : 10_000,
    retry: false,
  });
  const results = useQuery({
    queryKey: [...QUERY, "results", roundId, offset],
    queryFn: () => getSocCorpusRoundResults(roundId, offset),
    enabled: !!roundId,
    refetchInterval:
      progress.data?.round.state === "running" || progress.data?.active_count
        ? 3_000
        : false,
    retry: false,
  });
  const round = progress.data?.round;
  const sameBatch =
    round?.selection.batch === batch && round.experiment_id === experimentId;
  const current = sameBatch ? progress.data : undefined;
  const canCompareToCurrent =
    !!current && current.round.state !== "running" && !current.active_count;

  useEffect(() => {
    try {
      const saved = JSON.parse(
        sessionStorage.getItem("soc.corpus.experiment") ?? "null",
      ) as { id?: string; offset?: number } | null;
      if (typeof saved?.id === "string") setExperimentId(saved.id);
      if (Number.isSafeInteger(saved?.offset) && saved!.offset! >= 0)
        setExperimentOffset(saved!.offset!);
    } catch {
      /* Optional browser storage. */
    }
    setRestored(true);
  }, []);
  useEffect(() => {
    if (restored && !experimentId && experiments.data?.length)
      setExperimentId(experiments.data[0]!.experiment_id);
  }, [restored, experimentId, experiments.data]);
  useEffect(() => {
    if (!restored || !experimentId) return;
    try {
      sessionStorage.setItem(
        "soc.corpus.experiment",
        JSON.stringify({ id: experimentId, offset: experimentOffset }),
      );
    } catch {
      /* Optional browser storage. */
    }
  }, [restored, experimentId, experimentOffset]);
  useEffect(() => {
    if (!sameBatch || !round || controlsRound.current === round.round_id)
      return;
    controlsRound.current = round.round_id;
    setLimit(String(round.execution_limit));
    setConcurrency(String(round.concurrency));
  }, [sameBatch, round]);
  useEffect(() => {
    try {
      setRoundId(
        sessionStorage.getItem(`soc.corpus.round:${experimentId}:${batch}`) ??
          "",
      );
    } catch {
      setRoundId("");
    }
    setRoundOffset(0);
    setOffset(0);
    setDetail(null);
  }, [batch, experimentId]);
  useEffect(() => {
    if (!experimentId || !roundId || !sameBatch) return;
    try {
      sessionStorage.setItem(
        `soc.corpus.round:${experimentId}:${batch}`,
        roundId,
      );
    } catch {
      /* Optional browser storage. */
    }
  }, [batch, experimentId, roundId, sameBatch]);
  useEffect(() => {
    if (!requestedAlert) return;
    setAlertId(requestedAlert.alertId);
    setLimit("1");
    setOpen(true);
    onRequestHandled();
  }, [requestedAlert, onRequestHandled]);

  const refresh = async () => {
    await cache.invalidateQueries({ queryKey: QUERY });
  };
  const mutation = useMutation({
    mutationFn: async (
      command: "prepare" | "create" | "start" | "pause" | "retry",
    ) => {
      if (command === "prepare") {
        prepareId.current ??= `EXP-${crypto.randomUUID()}`;
        const result = await prepareSocCorpusExperiment(
          prepareId.current,
          name.trim() || "经验积累与效果验证",
        );
        setExperimentId(result.experiment_id);
        setExperimentOffset(0);
        setPrepareOpen(false);
        prepareId.current = null;
        return;
      }
      if (command === "create") {
        if (!configuration.data || !experimentId)
          throw new Error("请先准备实验名单");
        const options =
          purpose === "memory"
            ? configuration.data.defaults
            : configuration.data.full_flow_defaults;
        const body = {
          experiment_id: experimentId,
          selection: corpusRoundSelection(batch, tier, groupId, alertId),
          purpose,
          memory_mode: memoryMode,
          parent_round_id:
            compareToCurrent && canCompareToCurrent ? roundId : null,
          options,
          execution_limit: limit === "all" ? 2_147_483_647 : Number(limit),
          concurrency: Math.min(
            Number(concurrency),
            configuration.data.max_concurrency,
          ),
        };
        const encoded = JSON.stringify(body);
        if (commandKey.current?.body !== encoded)
          commandKey.current = { body: encoded, key: crypto.randomUUID() };
        const result = await createSocCorpusRound(body, commandKey.current.key);
        setRoundId(result.round_id);
        setOffset(0);
        setDetail(null);
        setOpen(false);
        setCompareToCurrent(false);
        commandKey.current = null;
        // Submission is deliberately separate from starting model work.
        toast.success("轮次已准备，请核对范围后点击开始运行");
        return;
      }
      if (!roundId) throw new Error("请先选择轮次");
      if (command === "pause") await pauseSocCorpusRound(roundId);
      else if (command === "retry") {
        const result = await retrySocCorpusRound(roundId);
        toast.success(
          `已重试 ${result.retried} 条；${result.not_retryable} 条需要检查失败原因`,
        );
      } else
        await startSocCorpusRound(roundId, {
          execution_limit:
            limit === "all"
              ? 2_147_483_647
              : Math.max(Number(limit), round?.execution_limit ?? 1),
          concurrency: Number(concurrency),
        });
    },
    onSuccess: refresh,
    onError: (error) => toast.error(error.message),
  });
  const error =
    experiments.error ??
    configuration.error ??
    rounds.error ??
    progress.error ??
    results.error;
  const visibleRounds = (rounds.data ?? []).filter(
    (item) => item.batch === batch,
  );

  return (
    <section
      className="max-w-full min-w-0 border-b px-5 py-4 md:px-7"
      aria-label="批次执行"
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-64 max-w-full">
          <label
            className="mb-1.5 block text-xs font-medium"
            htmlFor="soc-experiment"
          >
            实验
          </label>
          <Select
            value={experimentId}
            onValueChange={(value) => {
              setExperimentId(value);
              setRoundOffset(0);
            }}
          >
            <SelectTrigger id="soc-experiment" className="w-full">
              <SelectValue placeholder="尚未准备实验" />
            </SelectTrigger>
            <SelectContent>
              {experiments.data?.map((item) => (
                <SelectItem key={item.experiment_id} value={item.experiment_id}>
                  {item.name} · {item.created_at.slice(0, 10)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="outline"
          onClick={() => setPrepareOpen(true)}
          disabled={mutation.isPending || !!error}
        >
          <PlusIcon className="size-4" />
          准备新实验
        </Button>
        <div className="w-72 max-w-full">
          <label
            className="mb-1.5 block text-xs font-medium"
            htmlFor="soc-round"
          >
            执行轮次
          </label>
          <Select
            value={sameBatch ? roundId : ""}
            onValueChange={(value) => {
              setRoundId(value);
              setOffset(0);
              setDetail(null);
            }}
          >
            <SelectTrigger id="soc-round" className="w-full">
              <SelectValue placeholder="选择本批次的执行轮次" />
            </SelectTrigger>
            <SelectContent>
              {visibleRounds.map((item) => (
                <SelectItem key={item.round_id} value={item.round_id}>
                  {new Date(item.created_at).toLocaleString("zh-CN")} ·{" "}
                  {corpusRoundStateLabel[item.state]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          onClick={() => {
            setAlertId("");
            setOpen(true);
          }}
          disabled={!experimentId || !configuration.data || mutation.isPending}
        >
          <PlusIcon className="size-4" />
          准备本批轮次
        </Button>
        <Button
          variant="outline"
          size="icon"
          title="刷新轮次"
          aria-label="刷新轮次"
          onClick={() => void refresh()}
        >
          <RefreshCwIcon className="size-4" />
        </Button>
        {experimentId && (
          <Button variant="outline" asChild>
            <Link
              href={`/workspace/soc/review/memory-candidates?experiment=${encodeURIComponent(experimentId)}`}
            >
              <ClipboardCheckIcon className="size-4" />
              审核本实验经验
            </Link>
          </Button>
        )}
        {(roundOffset > 0 || rounds.data?.length === 50) && (
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="icon"
              title="较新轮次"
              aria-label="较新轮次"
              disabled={!roundOffset}
              onClick={() => setRoundOffset(Math.max(0, roundOffset - 50))}
            >
              <ArrowLeftIcon className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              title="更早轮次"
              aria-label="更早轮次"
              disabled={rounds.data?.length !== 50}
              onClick={() => setRoundOffset(roundOffset + 50)}
            >
              <ArrowRightIcon className="size-4" />
            </Button>
          </div>
        )}
        {(experimentOffset > 0 || experiments.data?.length === 50) && (
          <Button
            variant="outline"
            onClick={() => {
              setExperimentOffset(
                experiments.data?.length === 50 ? experimentOffset + 50 : 0,
              );
              setExperimentId("");
            }}
          >
            其他实验
          </Button>
        )}
      </div>
      {error && (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {error.message}
        </p>
      )}
      {current && round && (
        <div className="mt-4 space-y-3">
          <div
            className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm"
            aria-live="polite"
          >
            <Badge variant="outline">
              {corpusRoundStateLabel[round.state]}
            </Badge>
            <span>范围 {current.selected_count.toLocaleString()} 条</span>
            <span>本次额度 {current.admitted_count.toLocaleString()} 条</span>
            <span className="font-medium text-emerald-700">
              完成 {current.completed_count}
            </span>
            <span>运行中 {current.active_count}</span>
            <span
              className={
                current.failed_count ? "text-red-700" : "text-muted-foreground"
              }
            >
              失败 {current.failed_count}
            </span>
            <span>
              {round.selection.batch === "learning"
                ? "不使用已有经验"
                : round.memory_mode === "none"
                  ? "无经验对照"
                  : `已冻结 ${round.memory_snapshot.length} 条第一批经验`}
            </span>
            <span>
              企业策略：{round.options.tenant_policy_enabled ? "开启" : "关闭"}
            </span>
          </div>
          {current.timing && (
            <div className="text-muted-foreground flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
              <span
                className="inline-flex items-center gap-1.5"
                title="允许领取任务期间的墙钟时间，包含等待和服务离线时间，不是模型耗时。"
              >
                <ClockIcon className="size-3.5" />
                运行计时 {corpusDuration(current.timing.running_ms)}
              </span>
              <span>暂停累计 {corpusDuration(current.timing.paused_ms)}</span>
              {current.timing.estimate_status === "estimated" && (
                <span title="按本轮已结束任务（含失败）的平均处理速度估算，只针对当前额度；不是完成时间保证。">
                  当前额度预计剩余{" "}
                  {corpusDuration(
                    (current.timing.estimated_remaining_seconds ?? 0) * 1000,
                  )}
                </span>
              )}
              {current.timing.estimate_status === "insufficient_samples" && (
                <span>预计剩余：样本不足，暂不估算</span>
              )}
            </div>
          )}
          {round.state_reason && (
            <p className="text-sm text-amber-800">{round.state_reason}</p>
          )}
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-40">
              <label className="mb-1 block text-xs" htmlFor="round-limit">
                累计运行额度
              </label>
              <Select value={limit} onValueChange={setLimit}>
                <SelectTrigger id="round-limit" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Array.from(
                    new Set([
                      "1",
                      "5",
                      "50",
                      String(round.execution_limit),
                      "all",
                    ]),
                  ).map((value) => (
                    <SelectItem key={value} value={value}>
                      {value === "all" ? "本轮全部" : `${value} 条`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="w-24">
              <label className="mb-1 block text-xs" htmlFor="round-concurrency">
                并发数
              </label>
              <Input
                id="round-concurrency"
                type="number"
                min={1}
                max={configuration.data?.max_concurrency ?? 3}
                value={concurrency}
                onChange={(event) => setConcurrency(event.target.value)}
              />
            </div>
            {round.state === "running" ? (
              <Button
                variant="outline"
                disabled={mutation.isPending}
                onClick={() => mutation.mutate("pause")}
              >
                <PauseIcon className="size-4" />
                暂停接收新任务
              </Button>
            ) : (
              <Button
                disabled={
                  mutation.isPending ||
                  round.state === "blocked" ||
                  !Number(concurrency) ||
                  Number(concurrency) >
                    (configuration.data?.max_concurrency ?? 3) ||
                  (round.state === "completed" &&
                    Math.min(
                      limit === "all" ? current.selected_count : Number(limit),
                      current.selected_count,
                    ) <= current.admitted_count)
                }
                onClick={() => mutation.mutate("start")}
              >
                <PlayIcon className="size-4" />
                {round.state === "prepared"
                  ? "开始运行"
                  : current.completed_count === current.selected_count
                    ? "本轮已完成"
                    : "继续未完成任务"}
              </Button>
            )}
            {current.failed_count > 0 && (
              <Button
                variant="outline"
                disabled={mutation.isPending || round.state === "blocked"}
                onClick={() => mutation.mutate("retry")}
              >
                <RotateCcwIcon className="size-4" />
                重试可恢复失败
              </Button>
            )}
            <span className="text-muted-foreground text-xs break-all">
              {round.round_id}
            </span>
          </div>
          <Tabs key={round.round_id} defaultValue="results" className="min-w-0">
            {round.parent_round_id && (
              <TabsList aria-label="轮次结果视图">
                <TabsTrigger value="results">本轮结果</TabsTrigger>
                <TabsTrigger value="comparison">前后对照</TabsTrigger>
              </TabsList>
            )}
            <TabsContent value="results">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      {[
                        "告警",
                        "本轮状态",
                        "本轮处置",
                        "经验使用",
                        "耗时",
                        "结果与经验",
                      ].map((label) => (
                        <th key={label} className="px-3 py-2 font-medium">
                          {label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {results.data?.items.map((item) => (
                      <tr key={item.alert_id} className="border-b align-top">
                        <td className="px-3 py-3 font-mono">{item.alert_id}</td>
                        <td className="max-w-64 px-3 py-3">
                          <span>{JOB_LABELS[item.status] ?? item.status}</span>
                          {item.error_message && (
                            <p className="mt-1 text-xs break-words text-red-700">
                              {item.error_message}
                            </p>
                          )}
                          {item.snapshot_changed_during_run && (
                            <p className="text-xs text-amber-800">
                              运行中配置变更，不计入本轮效果
                            </p>
                          )}
                        </td>
                        <td className="px-3 py-3">
                          {handlingLabel(item.summary.recommended_handling)}
                        </td>
                        <td className="px-3 py-3">
                          {item.summary.processing_path === "memory"
                            ? "已复用审核结论"
                            : item.summary.memory_uses?.length
                              ? "仅作研判参考"
                              : "未使用"}
                        </td>
                        <td className="px-3 py-3 whitespace-nowrap tabular-nums">
                          {item.summary.total_duration_ms == null
                            ? "--"
                            : `${(item.summary.total_duration_ms / 1000).toFixed(1)} 秒`}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex flex-wrap gap-2">
                            {item.run_id && (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setDetail(item)}
                              >
                                <EyeIcon className="size-4" />
                                本轮结果
                              </Button>
                            )}
                            {item.candidate_id && (
                              <Button asChild size="sm">
                                <Link
                                  href={`/workspace/soc/review/memory-candidates/${encodeURIComponent(item.candidate_id)}?experiment=${encodeURIComponent(round.experiment_id)}`}
                                >
                                  审核经验
                                </Link>
                              </Button>
                            )}
                            {item.run_id &&
                              item.status === "completed" &&
                              !item.candidate_id &&
                              round.selection.batch === "learning" && (
                                <Button asChild variant="outline" size="sm">
                                  <Link
                                    href={`/workspace/soc/alerts?run_id=${encodeURIComponent(item.run_id)}`}
                                  >
                                    <EyeIcon className="size-4" />
                                    查看研判 / 提炼经验
                                  </Link>
                                </Button>
                              )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center justify-end gap-2 text-xs">
                <span>
                  {results.data?.total
                    ? `${offset + 1}–${Math.min(offset + 20, results.data.total)} / ${results.data.total}`
                    : "暂无任务"}
                </span>
                <Button
                  size="icon-sm"
                  variant="outline"
                  title="上一页结果"
                  aria-label="上一页结果"
                  disabled={!offset}
                  onClick={() => setOffset(Math.max(0, offset - 20))}
                >
                  <ArrowLeftIcon className="size-4" />
                </Button>
                <Button
                  size="icon-sm"
                  variant="outline"
                  title="下一页结果"
                  aria-label="下一页结果"
                  disabled={offset + 20 >= (results.data?.total ?? 0)}
                  onClick={() => setOffset(offset + 20)}
                >
                  <ArrowRightIcon className="size-4" />
                </Button>
              </div>
              {detail?.run_id && (
                <div className="border-t pt-3">
                  <h3 className="mb-2 text-sm font-medium">
                    告警 {detail.alert_id} · 本轮研判记录
                  </h3>
                  <AuditViewer
                    key={detail.run_id}
                    alertId={detail.alert_id}
                    runId={detail.run_id}
                  />
                </div>
              )}
            </TabsContent>
            {round.parent_round_id && (
              <TabsContent value="comparison">
                <SocCorpusRoundComparison
                  key={round.round_id}
                  roundId={round.round_id}
                  active={round.state === "running" || !!current.active_count}
                />
              </TabsContent>
            )}
          </Tabs>
        </div>
      )}
      <Dialog open={prepareOpen} onOpenChange={setPrepareOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>准备实验名单</DialogTitle>
            <DialogDescription>
              固定当前样本的两批划分，不运行告警，不删除已有数据。
            </DialogDescription>
          </DialogHeader>
          <label htmlFor="experiment-name" className="text-sm">
            实验名称
          </label>
          <Input
            id="experiment-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="经验积累与效果验证"
            maxLength={160}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setPrepareOpen(false)}>
              取消
            </Button>
            <Button
              disabled={mutation.isPending}
              onClick={() => mutation.mutate("prepare")}
            >
              确认准备
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {batch === "learning" ? "准备沉淀经验轮次" : "准备效果验证轮次"}
            </DialogTitle>
            <DialogDescription>
              {groupId === "all"
                ? "范围：本批次全部分组"
                : `范围：当前同类组 ${groupId}`}
            </DialogDescription>
          </DialogHeader>
          {!experimentId && (
            <p className="text-sm text-amber-800">
              请先关闭此窗口，准备实验名单。
            </p>
          )}
          {canCompareToCurrent && (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="accent-primary size-4"
                checked={compareToCurrent}
                onChange={(event) => setCompareToCurrent(event.target.checked)}
              />
              作为当前轮次的复测，保留旧结果对照
            </label>
          )}
          <label className="text-sm" htmlFor="round-alert">
            指定告警（可选）
          </label>
          <Input
            id="round-alert"
            value={alertId}
            onChange={(event) => setAlertId(event.target.value)}
            placeholder="留空选择当前范围"
          />
          <label className="text-sm" htmlFor="round-purpose">
            验证目的
          </label>
          <Select
            value={purpose}
            onValueChange={(value) => setPurpose(value as typeof purpose)}
          >
            <SelectTrigger id="round-purpose" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="memory">经验效果（关闭企业策略）</SelectItem>
              <SelectItem value="full_flow">
                完整流程（沿用企业策略配置）
              </SelectItem>
            </SelectContent>
          </Select>
          {batch === "validation" && (
            <>
              <label className="text-sm" htmlFor="round-memory">
                经验来源
              </label>
              <Select
                value={memoryMode}
                onValueChange={(value) =>
                  setMemoryMode(value as typeof memoryMode)
                }
              >
                <SelectTrigger id="round-memory" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="snapshot">
                    冻结第一批已确认且开放的经验
                  </SelectItem>
                  <SelectItem value="none">不使用经验（对照轮）</SelectItem>
                </SelectContent>
              </Select>
            </>
          )}
          <label className="text-sm" htmlFor="create-limit">
            本次先运行
          </label>
          <Select value={limit} onValueChange={setLimit}>
            <SelectTrigger id="create-limit" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {["1", "5", "50", "all"].map((value) => (
                <SelectItem key={value} value={value}>
                  {value === "all" ? "当前范围全部" : `${value} 条`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              disabled={
                !experimentId || !configuration.data || mutation.isPending
              }
              onClick={() => mutation.mutate("create")}
            >
              确认范围并准备
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
