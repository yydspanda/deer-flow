"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeftIcon, ArrowRightIcon, EyeIcon } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getSocCorpusRoundComparison } from "@/core/soc/api";
import {
  corpusDuration,
  type SocCorpusComparisonSide,
} from "@/core/soc/corpus-experiments";

const AuditViewer = dynamic(() =>
  import("./soc-corpus-audit-viewer").then(
    (module) => module.SocCorpusAuditViewer,
  ),
);
const LABELS = {
  handling_changed: "处置改变",
  handling_unchanged: "处置一致",
  not_comparable: "暂不可比较",
  new_sample: "旧轮次无此告警",
  not_captured: "旧结果未留存",
};

function ResultSide({
  title,
  result,
  onOpen,
}: {
  title: string;
  result: SocCorpusComparisonSide | null;
  onOpen: () => void;
}) {
  const handling = result?.summary.recommended_handling;
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">{title}</span>
        <strong>
          {!result
            ? "无对应记录"
            : result.status === "failed"
              ? "运行失败"
              : handling === "ignore"
                ? "忽略"
                : handling === "transfer"
                  ? "转交"
                  : "尚无处置结果"}
        </strong>
      </div>
      {result && (
        <>
          <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
            <span>耗时 {corpusDuration(result.summary.total_duration_ms)}</span>
            <span>
              Token{" "}
              {result.summary.measurements.total_tokens?.toLocaleString() ??
                "未记录"}
            </span>
            <span>
              模型调用{" "}
              {result.summary.measurements.provider_call_count ?? "未记录"}
            </span>
          </div>
          <div className="space-y-1 text-xs">
            {result.summary.memory_uses.length ? (
              result.summary.memory_uses.map((use, index) => (
                <div
                  key={`${use.memory_id}-${index}`}
                  className="flex flex-wrap gap-x-2 gap-y-1"
                >
                  <span>
                    {use.directive_applied ? "复用审核结论" : "仅作研判参考"}
                  </span>
                  <Link
                    className="break-all underline underline-offset-2"
                    href={`/workspace/soc/memory/records/${encodeURIComponent(use.memory_id)}`}
                  >
                    {use.memory_id}
                    {use.memory_version != null
                      ? ` · v${use.memory_version}`
                      : ""}
                  </Link>
                </div>
              ))
            ) : (
              <span className="text-muted-foreground">未使用历史经验</span>
            )}
          </div>
          {result.error_message && (
            <p className="text-xs break-words text-red-700">
              {result.error_message}
            </p>
          )}
          {result.snapshot_changed_during_run && (
            <p className="text-xs text-amber-800">
              运行中配置或经验变更，不计入效果对比
            </p>
          )}
          {result.run_id && (
            <Button size="sm" variant="outline" onClick={onOpen}>
              <EyeIcon className="size-4" />
              查看{title}
            </Button>
          )}
        </>
      )}
    </div>
  );
}

export function SocCorpusRoundComparison({
  roundId,
  active,
}: {
  roundId: string;
  active: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const [detail, setDetail] = useState<{
    alertId: string;
    runId: string;
    title: string;
  } | null>(null);
  const comparison = useQuery({
    queryKey: ["soc-corpus-experiments", "comparison", roundId, offset],
    queryFn: () => getSocCorpusRoundComparison(roundId, offset),
    refetchInterval: active ? 3_000 : false,
    retry: false,
  });
  if (comparison.isPending)
    return (
      <p role="status" className="text-muted-foreground py-4 text-sm">
        正在读取对照结果…
      </p>
    );
  if (comparison.error)
    return (
      <p role="alert" className="py-4 text-sm text-red-700">
        对照结果读取失败：{comparison.error.message}
      </p>
    );
  const data = comparison.data;
  return (
    <section aria-label="前后结果对照" className="min-w-0">
      <div className="text-muted-foreground flex flex-wrap items-center gap-2 border-b py-3 text-xs">
        <span className="break-all">对照轮次 {data.parent_round_id}</span>
        {data.config_changed && <Badge variant="outline">运行配置不同</Badge>}
      </div>
      {data.items.map((item) => (
        <div
          key={item.alert_id}
          className="grid min-w-0 gap-4 border-b py-4 md:grid-cols-[minmax(120px,0.5fr)_minmax(0,1fr)_minmax(0,1fr)]"
        >
          <div className="flex flex-wrap items-start gap-2 md:flex-col">
            <span className="text-sm font-medium">告警 {item.alert_id}</span>
            <Badge
              variant={
                item.comparison_status === "handling_changed"
                  ? "default"
                  : "outline"
              }
            >
              {LABELS[item.comparison_status]}
            </Badge>
            {item.changed_fields.includes("memory_uses") && (
              <span className="text-muted-foreground text-xs">
                经验使用有变化
              </span>
            )}
          </div>
          <ResultSide
            title="旧结果"
            result={item.before}
            onOpen={() =>
              item.before?.run_id &&
              setDetail({
                alertId: item.alert_id,
                runId: item.before.run_id,
                title: "旧结果",
              })
            }
          />
          <ResultSide
            title="复测结果"
            result={item.after}
            onOpen={() =>
              item.after.run_id &&
              setDetail({
                alertId: item.alert_id,
                runId: item.after.run_id,
                title: "复测结果",
              })
            }
          />
        </div>
      ))}
      <div className="flex items-center justify-end gap-2 py-3 text-xs">
        <span>
          {data.total
            ? `${offset + 1}–${Math.min(offset + 20, data.total)} / ${data.total}`
            : "暂无对照任务"}
        </span>
        <Button
          size="icon-sm"
          variant="outline"
          title="上一页对照"
          aria-label="上一页对照"
          disabled={!offset}
          onClick={() => {
            setOffset(Math.max(0, offset - 20));
            setDetail(null);
          }}
        >
          <ArrowLeftIcon className="size-4" />
        </Button>
        <Button
          size="icon-sm"
          variant="outline"
          title="下一页对照"
          aria-label="下一页对照"
          disabled={offset + 20 >= data.total}
          onClick={() => {
            setOffset(offset + 20);
            setDetail(null);
          }}
        >
          <ArrowRightIcon className="size-4" />
        </Button>
      </div>
      {detail && (
        <div className="border-t pt-3">
          <h3 className="mb-2 text-sm font-medium">
            告警 {detail.alertId} · {detail.title}
          </h3>
          <AuditViewer
            key={detail.runId}
            alertId={detail.alertId}
            runId={detail.runId}
          />
        </div>
      )}
    </section>
  );
}
