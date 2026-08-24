"use client";

import {
  AlertTriangleIcon,
  BracesIcon,
  CheckCircle2Icon,
  DownloadIcon,
  EyeIcon,
  ListTreeIcon,
  Minimize2Icon,
  RefreshCwIcon,
  XCircleIcon,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSocCorpusWorkbenchAudit } from "@/core/soc";
import type {
  SocCorpusWorkbenchAuditArtifact,
  SocCorpusWorkbenchAuditArtifactStatus,
} from "@/core/soc";
import { cn } from "@/lib/utils";

const STATUS_LABELS: Record<SocCorpusWorkbenchAuditArtifactStatus, string> = {
  available: "完整",
  partial: "部分可用",
  unavailable: "不可用",
};

const SocJsonCodeViewer = dynamic(
  () =>
    import("./soc-json-code-viewer").then((module) => module.SocJsonCodeViewer),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-[420px] items-center justify-center bg-zinc-950 font-mono text-xs text-zinc-400">
        Loading JSON viewer…
      </div>
    ),
  },
);

const METRIC_LABELS: Record<string, string> = {
  run_status: "Run 状态",
  pipeline: "流水线",
  model: "模型",
  steps: "步骤",
  duration_ms: "总耗时 ms",
  payload_bytes: "原始输入 bytes",
  top_level_fields: "顶层字段",
  raw_message_count: "Message 数量",
  raw_message_chars: "Message 字符",
  adapter: "Adapter",
  message_schemas: "Message Schema",
  selected_layer: "选中证据层",
  canonical_fields: "Canonical 字段",
  parsed_messages: "解析 Message",
  mentions: "实体提及",
  entity_types: "实体类型",
  warnings: "警告",
  field_trusts: "字段信任",
  role_claims: "角色声明",
  role_resolutions: "角色裁决",
  scenario_hypotheses: "场景假设",
  conflicts: "冲突",
  evidence_catalog: "E-* 证据",
  context_catalog: "上下文",
  selected_skills: "Skills",
  projected_fields: "模型投影字段",
  omissions: "有记录的省略",
  high_value_gaps: "高价值缺口",
  verdict: "结论",
  confidence: "置信度",
  reasoning_items: "推理项",
  scenarios: "场景",
  provider_attempts: "模型调用",
  output_quality: "输出质量",
  grounded: "引用通过",
  rejected: "引用拒绝",
  decision_usable: "决策可用",
  review_required: "需要复核",
  role_verification: "角色复核",
  needs_review: "需要复核",
  review_items: "ReviewQueue",
  decision_transitions: "决策变更",
  observation: "Observation",
  support_count: "支持样本",
  distinct_sources: "独立来源",
  candidates: "Candidates",
  memory_records: "Memory",
  memory_uses: "本次召回",
};

function artifactStatusIcon(status: SocCorpusWorkbenchAuditArtifactStatus) {
  if (status === "available") {
    return <CheckCircle2Icon className="size-4 text-emerald-600" />;
  }
  if (status === "partial") {
    return <AlertTriangleIcon className="size-4 text-amber-600" />;
  }
  return <XCircleIcon className="size-4 text-zinc-500" />;
}

function statusClass(status: SocCorpusWorkbenchAuditArtifactStatus) {
  if (status === "available") {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  if (status === "partial") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  return "border-zinc-300 bg-zinc-50 text-zinc-700";
}

function stringifyJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function downloadJson(fileName: string, value: unknown) {
  const blob = new Blob([stringifyJson(value)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}

function JsonArtifact({
  fileName,
  value,
}: {
  fileName: string;
  value: unknown;
}) {
  return <SocJsonCodeViewer fileName={fileName} value={value} />;
}

function ArtifactPayload({
  artifact,
}: {
  artifact: SocCorpusWorkbenchAuditArtifact;
}) {
  const hasSplitModelInput =
    artifact.artifact_id === "bounded-analysis-input" &&
    Object.hasOwn(artifact.payload, "model_visible_context") &&
    Object.hasOwn(artifact.payload, "runtime_request_audit");
  const [view, setView] = useState<"model" | "runtime">("model");
  const projectionLineage = artifact.payload.projection_lineage;
  const modelProjectionIsExact =
    typeof projectionLineage === "object" &&
    projectionLineage !== null &&
    "exact" in projectionLineage &&
    projectionLineage.exact === true;

  useEffect(() => {
    setView("model");
  }, [artifact.artifact_id]);

  if (!hasSplitModelInput) {
    return (
      <JsonArtifact fileName={artifact.file_name} value={artifact.payload} />
    );
  }

  const value =
    view === "model"
      ? {
          projection_lineage: artifact.payload.projection_lineage,
          model_visible_context: artifact.payload.model_visible_context,
        }
      : artifact.payload.runtime_request_audit;
  const fileName =
    view === "model"
      ? "06a-model-visible-context.json"
      : "06b-runtime-request-audit.json";

  return (
    <div>
      <div className="border-y bg-white px-4 py-2 dark:bg-zinc-950">
        <Tabs
          value={view}
          onValueChange={(next) =>
            setView(next === "runtime" ? "runtime" : "model")
          }
        >
          <TabsList>
            <TabsTrigger value="model">
              {modelProjectionIsExact ? "模型实际可见" : "模型可见投影（重建）"}
            </TabsTrigger>
            <TabsTrigger value="runtime">Runtime 审计契约</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <JsonArtifact fileName={fileName} value={value} />
    </div>
  );
}

function ArtifactDetail({
  artifact,
}: {
  artifact: SocCorpusWorkbenchAuditArtifact;
}) {
  return (
    <article className="min-w-0">
      <header className="px-5 py-5 md:px-7">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-mono">
            {String(artifact.sequence).padStart(2, "0")}
          </Badge>
          <h4 className="text-base font-semibold">{artifact.title}</h4>
          <Badge variant="outline" className={statusClass(artifact.status)}>
            {STATUS_LABELS[artifact.status]}
          </Badge>
          <Badge variant="outline">{artifact.source}</Badge>
        </div>
        <p className="text-muted-foreground mt-2 max-w-4xl text-sm leading-6">
          {artifact.description}
        </p>
      </header>

      {Object.keys(artifact.metrics).length ? (
        <div className="grid border-y bg-zinc-50 sm:grid-cols-2 xl:grid-cols-4">
          {Object.entries(artifact.metrics).map(([key, value]) => (
            <div key={key} className="min-w-0 border-r border-b px-4 py-3">
              <p className="text-muted-foreground text-xs">
                {METRIC_LABELS[key] ?? key}
              </p>
              <p className="mt-1 font-mono text-sm break-all">
                {typeof value === "boolean"
                  ? value
                    ? "yes"
                    : "no"
                  : String(value)}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      {artifact.review_guide.length ? (
        <div className="border-b bg-sky-50 px-5 py-4 md:px-7">
          <p className="text-sm font-semibold">审计关注点 / Review Focus</p>
          <ol className="mt-2 space-y-1.5 text-sm leading-6 text-sky-950">
            {artifact.review_guide.map((item, index) => (
              <li key={item}>
                <span className="mr-2 font-mono text-xs text-sky-700">
                  {index + 1}.
                </span>
                {item}
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      <ArtifactPayload artifact={artifact} />
    </article>
  );
}

export function SocCorpusAuditViewer({
  alertId,
  runId,
}: {
  alertId: string;
  runId?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"artifacts" | "bundle">("artifacts");
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(
    null,
  );
  const query = useSocCorpusWorkbenchAudit(alertId, {
    enabled: open && !!runId,
  });

  useEffect(() => {
    setOpen(false);
    setView("artifacts");
    setSelectedArtifactId(null);
  }, [alertId, runId]);

  useEffect(() => {
    if (!query.audit?.artifacts.length) return;
    if (
      selectedArtifactId &&
      query.audit.artifacts.some(
        (item) => item.artifact_id === selectedArtifactId,
      )
    ) {
      return;
    }
    setSelectedArtifactId(query.audit.artifacts[0]?.artifact_id ?? null);
  }, [query.audit, selectedArtifactId]);

  const audit = query.audit;
  const selectedArtifact =
    audit?.artifacts.find((item) => item.artifact_id === selectedArtifactId) ??
    null;

  return (
    <section className="border-b" aria-label="SOC DEV 全链路审计">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 md:px-7">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <EyeIcon className="size-4" />
            <h3 className="text-sm font-semibold">全链路审计 / Full Audit</h3>
            <Badge variant="outline" className="border-red-300 text-red-700">
              DEV / MOCK
            </Badge>
            <Badge variant="outline">管理员可见</Badge>
          </div>
          <p className="text-muted-foreground mt-1 text-xs">
            {runId
              ? `${runId} · 读取持久化产物，不重新运行模型`
              : "运行告警后生成可审计阶段产物"}
          </p>
        </div>
        <Button
          variant={open ? "secondary" : "outline"}
          size="sm"
          disabled={!runId}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? (
            <Minimize2Icon className="size-4" />
          ) : (
            <EyeIcon className="size-4" />
          )}
          {open ? "收起完整审计" : "打开完整审计"}
        </Button>
      </div>

      {open && query.isLoading ? (
        <div className="space-y-3 border-t px-5 py-5 md:px-7">
          <Skeleton className="h-14 w-full rounded-md" />
          <Skeleton className="h-96 w-full rounded-md" />
        </div>
      ) : null}

      {open && query.error ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-red-200 bg-red-50 px-5 py-4 text-sm text-red-900 md:px-7">
          <span>
            {query.error instanceof Error
              ? query.error.message
              : "完整审计产物加载失败"}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
          >
            <RefreshCwIcon className="size-4" />
            重试
          </Button>
        </div>
      ) : null}

      {open && audit ? (
        <div className="border-t">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-red-50 px-5 py-3 text-xs text-red-950 md:px-7">
            <span>
              包含完整原始告警、解析投影和模型上下文，仅用于当前隔离 DEV
              审计与演示。
            </span>
            <span className="font-mono">
              {audit.pipeline_version} · {audit.model_name} ·{" "}
              {audit.prompt_version}
            </span>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 md:px-7">
            <Tabs
              value={view}
              onValueChange={(value) =>
                setView(value === "bundle" ? "bundle" : "artifacts")
              }
            >
              <TabsList>
                <TabsTrigger value="artifacts">
                  <ListTreeIcon className="size-4" />
                  阶段产物
                </TabsTrigger>
                <TabsTrigger value="bundle">
                  <BracesIcon className="size-4" />
                  完整 Bundle JSON
                </TabsTrigger>
              </TabsList>
            </Tabs>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                downloadJson(
                  `${audit.alert_id}.${audit.run_id}.audit-bundle.json`,
                  audit,
                )
              }
            >
              <DownloadIcon className="size-4" />
              下载全部
            </Button>
          </div>

          {view === "artifacts" ? (
            <div className="grid min-w-0 border-t lg:grid-cols-[280px_minmax(0,1fr)]">
              <nav
                className="flex overflow-x-auto border-b bg-zinc-50 lg:block lg:border-r lg:border-b-0"
                aria-label="审计阶段产物"
              >
                {audit.artifacts.map((artifact) => (
                  <button
                    key={artifact.artifact_id}
                    type="button"
                    className={cn(
                      "flex min-w-[250px] items-start gap-3 border-r px-4 py-3 text-left lg:w-full lg:min-w-0 lg:border-r-0 lg:border-b",
                      artifact.artifact_id === selectedArtifactId
                        ? "bg-white shadow-[inset_3px_0_0_0_var(--color-sky-600)]"
                        : "hover:bg-white",
                    )}
                    onClick={() => setSelectedArtifactId(artifact.artifact_id)}
                  >
                    <span className="mt-0.5 shrink-0">
                      {artifactStatusIcon(artifact.status)}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">
                        {artifact.title}
                      </span>
                      <span className="text-muted-foreground mt-1 block font-mono text-xs">
                        {artifact.file_name}
                      </span>
                    </span>
                  </button>
                ))}
              </nav>
              {selectedArtifact ? (
                <ArtifactDetail artifact={selectedArtifact} />
              ) : null}
            </div>
          ) : (
            <div className="border-t">
              <JsonArtifact
                fileName={`${audit.alert_id}.${audit.run_id}.audit-bundle.json`}
                value={audit}
              />
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
