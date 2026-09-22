"use client";

import {
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  getSocCorpusExperimentConfiguration,
  getSocCorpusQuickState,
  getSocCorpusQuickHistory,
  runSocCorpusQuick,
  updateSocCorpusConcurrency,
} from "@/core/soc/api";
import type {
  SocCorpusExperimentConfiguration,
  SocCorpusQuickState,
} from "@/core/soc/corpus-experiments";
import type {
  SocAnalysisExecutionOptions,
  SocCorpusBatch,
  SocCorpusWorkbenchRunControls,
} from "@/core/soc/types";

import {
  availableCorpusRunSettings,
  SocCorpusRunSettings,
} from "./soc-corpus-run-settings";

const QUERY = ["soc-corpus-quick"] as const;
const CONFIGURATION_QUERY = [
  "soc-corpus-experiments",
  "configuration",
] as const;

async function invalidateWorkbenchProgress(cache: QueryClient) {
  // Audit bundles are explicit, run-pinned reads and must not follow batch progress.
  await Promise.all(
    ["state", "activity", "execution"].map((kind) =>
      cache.invalidateQueries({ queryKey: ["soc-corpus-workbench", kind] }),
    ),
  );
}

export function SocCorpusExperiments({
  batch,
  tier,
  controls,
  requestedAlert,
  onRequestHandled,
  alertIds = [],
  onStateUpdated,
}: {
  batch: SocCorpusBatch;
  tier: "main" | "supplementary" | "all";
  groupId?: string;
  controls?: SocCorpusWorkbenchRunControls | null;
  requestedAlert: {
    alertId: string;
    key: number;
    jobId?: string | null;
    rerun?: boolean;
  } | null;
  onRequestHandled: () => void;
  alertIds?: string[];
  onStateUpdated?: (state: SocCorpusQuickState) => void;
}) {
  const cache = useQueryClient();
  // Reload restores the server's saved batch options. Only explicit edits in
  // this page override them, and drafts must not spill into the other batch.
  const [settingsByBatch, setSettingsByBatch] = useState<
    Partial<Record<SocCorpusBatch, SocAnalysisExecutionOptions>>
  >({});
  const [concurrencyDraft, setConcurrencyDraft] = useState<number | null>(null);
  const handled = useRef<number | null>(null);
  const completion = useRef("");
  const scope =
    batch === "learning" || tier === "all"
      ? "all"
      : tier === "main"
        ? "reuse"
        : "explore";
  const configuration = useQuery({
    queryKey: [...CONFIGURATION_QUERY, batch],
    queryFn: () => getSocCorpusExperimentConfiguration(batch),
    refetchInterval: (query) =>
      query.state.data?.concurrency_limit !== undefined ||
      query.state.data?.can_configure === false ||
      controls?.can_configure === false
        ? 10000
        : false,
    retry: false,
  });
  const state = useQuery({
    queryKey: [...QUERY, batch, scope, alertIds.join(",")],
    queryFn: () => getSocCorpusQuickState(batch, scope, 0, alertIds),
    refetchInterval: (query) =>
      query.state.data?.running ||
      query.state.data?.active ||
      query.state.data?.manual_pending
        ? 2000
        : 10000,
    retry: false,
  });
  const canConfigure =
    configuration.data?.can_configure !== false &&
    controls?.can_configure !== false;
  const concurrency = useMutation({
    mutationFn: (value: number) => {
      if (!canConfigure) throw new Error("仅部署主机可修改运行设置");
      return updateSocCorpusConcurrency(value);
    },
    onSuccess: async (saved) => {
      // Fence old reads before projecting the acknowledged value across batches.
      await cache.cancelQueries({ queryKey: CONFIGURATION_QUERY });
      cache.setQueriesData<SocCorpusExperimentConfiguration>(
        { queryKey: CONFIGURATION_QUERY },
        (previous) => previous && { ...previous, ...saved },
      );
      setConcurrencyDraft(null);
      await Promise.all([
        cache.invalidateQueries({ queryKey: CONFIGURATION_QUERY }),
        cache.invalidateQueries({
          queryKey: ["soc-corpus-workbench", "activity"],
        }),
      ]);
      toast.success(`最大并发已保存为 ${saved.max_concurrency}`);
    },
    onError: () => {
      void configuration.refetch();
    },
  });
  const savedOptions =
    configuration.data?.saved_options ?? configuration.data?.defaults;
  const options =
    savedOptions && !configuration.isPlaceholderData
      ? !canConfigure
        ? savedOptions
        : controls
          ? availableCorpusRunSettings(
              settingsByBatch[batch] ?? savedOptions,
              controls,
            )
          : savedOptions
      : null;
  useEffect(() => {
    if (state.data) onStateUpdated?.(state.data);
  }, [state.data, onStateUpdated]);
  const mutation = useMutation({
    mutationFn: async ({
      action,
      alertId,
      jobId,
    }: {
      action: "start" | "pause" | "run" | "rerun";
      alertId?: string;
      jobId?: string | null;
    }) => {
      if (!options) throw new Error("运行设置尚未加载");
      return runSocCorpusQuick(
        { batch, scope, action, alert_id: alertId, options },
        action === "rerun" ? `quick-rerun:${jobId}` : undefined,
      );
    },
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: QUERY });
      await invalidateWorkbenchProgress(cache);
      await configuration.refetch();
    },
    onError: (error) => {
      toast.error(error.message);
      void configuration.refetch();
    },
  });
  const submit = mutation.mutate;
  useEffect(() => {
    if (!requestedAlert || !options || handled.current === requestedAlert.key)
      return;
    handled.current = requestedAlert.key;
    submit(
      {
        action: requestedAlert.rerun ? "rerun" : "run",
        alertId: requestedAlert.alertId,
        jobId: requestedAlert.jobId,
      },
      { onSettled: onRequestHandled },
    );
  }, [requestedAlert, options, onRequestHandled, submit]);
  const data = state.data;
  useEffect(() => {
    if (!data) return;
    const revision = `${batch}:${data.running}:${data.active}:${data.completed}:${data.failed}:${data.remaining}:${data.items.map((item) => `${item.job_id}:${item.status}`).join(",")}`;
    if (completion.current !== revision) {
      completion.current = revision;
      void invalidateWorkbenchProgress(cache);
    }
  }, [data, batch, cache]);
  const finished =
    !!data &&
    data.total > 0 &&
    data.remaining === 0 &&
    data.active === 0 &&
    !data.running;
  return (
    <section aria-label="批次执行" className="border-b px-5 py-4 md:px-7">
      {controls && options && configuration.data && (
        <SocCorpusRunSettings
          title="运行设置"
          resetTitle="恢复默认运行开关"
          controls={{
            ...controls,
            defaults: configuration.data.defaults,
            can_configure: canConfigure,
          }}
          value={options}
          onChange={(value) => {
            if (!canConfigure) return;
            setSettingsByBatch((previous) => ({ ...previous, [batch]: value }));
          }}
        >
          {configuration.data.concurrency_limit !== undefined && (
            <div className="mt-4 border-t pt-3">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                {canConfigure ? (
                  <>
                    <label className="flex items-center gap-2 font-medium">
                      最大并发
                      <select
                        aria-label="最大并发"
                        className="border-input bg-background rounded-md border px-3 py-1.5 font-normal"
                        value={
                          concurrencyDraft ?? configuration.data.max_concurrency
                        }
                        disabled={concurrency.isPending}
                        onChange={(event) =>
                          setConcurrencyDraft(Number(event.target.value))
                        }
                      >
                        {Array.from(
                          { length: configuration.data.concurrency_limit },
                          (_, index) => index + 1,
                        ).map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </label>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        concurrency.isPending ||
                        concurrencyDraft === null ||
                        concurrencyDraft === configuration.data.max_concurrency
                      }
                      onClick={() => {
                        if (concurrencyDraft !== null)
                          concurrency.mutate(concurrencyDraft);
                      }}
                    >
                      {concurrency.isPending ? "保存中…" : "保存并发"}
                    </Button>
                  </>
                ) : (
                  <span className="font-medium">最大并发</span>
                )}
                <span
                  className="text-muted-foreground text-xs"
                  aria-live="polite"
                >
                  当前生效：{configuration.data.max_concurrency}
                </span>
              </div>
              <p className="text-muted-foreground mt-2 text-xs">
                两批共享，保存后生效；降低后等待当前任务完成，不会中断研判。
              </p>
              {concurrency.error && (
                <p role="alert" className="mt-2 text-sm text-red-700">
                  {concurrency.error.message}
                </p>
              )}
            </div>
          )}
        </SocCorpusRunSettings>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          disabled={!options || mutation.isPending || !!state.error || finished}
          onClick={() => submit({ action: data?.running ? "pause" : "start" })}
        >
          {finished
            ? "已完成"
            : data?.running
              ? "暂停"
              : data && data.total > 0 && data.remaining < data.total
                ? "继续"
                : batch === "learning"
                  ? "开始积累"
                  : "开始验证"}
        </Button>
        {data && (
          <Button asChild variant="outline">
            <Link
              href={`/workspace/soc/review/memory-candidates?experiment=${encodeURIComponent(data.experiment_id)}&return_batch=${batch}`}
            >
              审核经验
            </Link>
          </Button>
        )}
        <span className="text-muted-foreground text-xs">
          列表筛选只影响浏览；已排队任务沿用保存的运行设置。
        </span>
      </div>
      {data && (
        <div aria-live="polite" className="mt-3 flex flex-wrap gap-5 text-sm">
          <span>完成 {data.completed}</span>
          <span>运行中 {data.active}</span>
          <span>剩余 {data.remaining}</span>
          <span>失败 {data.failed}</span>
          <span>待审核经验 {data.pending_candidates}</span>
        </div>
      )}
      {data?.blocked_reason && (
        <p role="alert" className="mt-2 text-sm text-amber-800">
          已停止：{data.blocked_reason}
          。请检查运行设置或审核经验；显式重新运行才会使用新设置。
        </p>
      )}
      {(state.error ?? configuration.error) && (
        <p role="alert">{(state.error ?? configuration.error)?.message}</p>
      )}
    </section>
  );
}

const HistoryAudit = dynamic(() =>
  import("./soc-corpus-audit-viewer").then(
    (module) => module.SocCorpusAuditViewer,
  ),
);

export function SocCorpusQuickHistory({ alertId }: { alertId: string }) {
  const [offset, setOffset] = useState(0);
  const [runId, setRunId] = useState<string | null>(null);
  const history = useQuery({
    queryKey: [...QUERY, "history", alertId, offset],
    queryFn: () => getSocCorpusQuickHistory(alertId, offset),
    refetchInterval: 5000,
  });
  return (
    <section aria-label="结果与历史记录" className="border-t p-5">
      <h3 className="mb-3 text-sm font-medium">结果与历史记录</h3>
      {history.error && <p role="alert">{history.error.message}</p>}
      <div className="flex flex-wrap gap-2">
        {history.data?.map((item) => (
          <Button
            key={item.job_id}
            variant="outline"
            disabled={!item.run_id}
            onClick={() => setRunId(item.run_id)}
          >
            {item.status === "completed"
              ? "查看结果"
              : item.status === "failed"
                ? "失败记录"
                : "运行中"}{" "}
            · {item.run_id ?? item.job_id}
          </Button>
        ))}
      </div>
      <div className="my-3 flex gap-2">
        <Button
          variant="ghost"
          disabled={!offset}
          onClick={() => setOffset(Math.max(0, offset - 20))}
        >
          较新记录
        </Button>
        <Button
          variant="ghost"
          disabled={history.data?.length !== 20}
          onClick={() => setOffset(offset + 20)}
        >
          更早记录
        </Button>
      </div>
      {runId && <HistoryAudit key={runId} alertId={alertId} runId={runId} />}
    </section>
  );
}
