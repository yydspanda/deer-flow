"use client";

import {
  BrainCircuitIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  DatabaseIcon,
  RefreshCwIcon,
  SearchIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

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
import { memoryFutureUseCopy } from "@/components/workspace/soc/soc-memory-copy";
import { SocWorkspaceHeader } from "@/components/workspace/soc/soc-workspace-header";
import { useSocMemoryRecordInventory } from "@/core/soc";
import type { SocMemoryRecordStatus } from "@/core/soc";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

const STATUS_LABELS: Record<SocMemoryRecordStatus, string> = {
  confirmed: "当前版本",
  deprecated: "已被替代",
  expired: "已过期",
};

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

function recordScope(record: {
  tenant_id?: string | null;
  tenant_scope: string;
}) {
  return record.tenant_id ?? record.tenant_scope;
}

function keyFacets(facets: Record<string, string[]>) {
  const priority = [
    "detection_key",
    "rule_code",
    "scenario_key",
    "attack_behavior_family",
    "network_service",
    "vulnerability_id",
    "environment",
  ];
  return priority.flatMap((key) =>
    (facets[key] ?? []).slice(0, 2).map((value) => `${key}: ${value}`),
  );
}

export function SocMemoryRecordInventory() {
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"all" | SocMemoryRecordStatus>("all");
  const [retrieval, setRetrieval] = useState<"all" | "enabled" | "disabled">(
    "all",
  );
  const [offset, setOffset] = useState(0);
  const { page, records, isLoading, isFetching, error, refetch } =
    useSocMemoryRecordInventory({
      status: status === "all" ? null : status,
      retrievalEnabled: retrieval === "all" ? null : retrieval === "enabled",
      search,
      limit: PAGE_SIZE,
      offset,
    });

  useEffect(() => {
    setOffset(0);
  }, [retrieval, search, status]);

  return (
    <div className="flex size-full min-h-0 flex-col">
      <SocWorkspaceHeader
        icon={DatabaseIcon}
        title="经验台账"
        description="查找、审计和修订已经由专家确认的 SOC 经验"
        actions={
          <>
            <Button size="sm" variant="outline" asChild>
              <Link href="/workspace/soc/memory">
                <BrainCircuitIcon className="size-4" />
                返回经验中心
              </Link>
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              title="刷新经验台账"
              aria-label="刷新经验台账"
              onClick={() => void refetch()}
              disabled={isFetching}
            >
              <RefreshCwIcon
                className={cn("size-4", isFetching && "animate-spin")}
              />
            </Button>
          </>
        }
      />

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-4 p-4 md:p-6">
          <section className="flex flex-wrap items-center gap-2 border px-3 py-3">
            <form
              className="flex min-w-0 flex-1 gap-2 sm:min-w-[28rem]"
              onSubmit={(event) => {
                event.preventDefault();
                setSearch(searchDraft.trim());
              }}
            >
              <Input
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                placeholder="经验 ID、告警 ID、规则、场景、CVE 或服务"
                aria-label="搜索经验台账"
              />
              <Button type="submit" size="icon" variant="outline" title="搜索">
                <SearchIcon className="size-4" />
              </Button>
            </form>
            <Select
              value={status}
              onValueChange={(value) =>
                setStatus(value as "all" | SocMemoryRecordStatus)
              }
            >
              <SelectTrigger className="w-36" aria-label="Memory 状态">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部版本</SelectItem>
                <SelectItem value="confirmed">当前版本</SelectItem>
                <SelectItem value="deprecated">已被替代</SelectItem>
                <SelectItem value="expired">已过期</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={retrieval}
              onValueChange={(value) =>
                setRetrieval(value as "all" | "enabled" | "disabled")
              }
            >
              <SelectTrigger className="w-40" aria-label="新告警使用状态">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部使用状态</SelectItem>
                <SelectItem value="enabled">已开放给新告警</SelectItem>
                <SelectItem value="disabled">暂停用于新告警</SelectItem>
              </SelectContent>
            </Select>
            <span className="text-muted-foreground ml-auto text-xs">
              排序：最近更新优先
            </span>
          </section>

          <section className="min-h-[34rem] border">
            <div className="bg-muted/30 grid grid-cols-[minmax(0,1fr)_9rem_10rem_10rem] gap-3 border-b px-4 py-2 text-xs font-medium max-lg:hidden">
              <span>业务经验</span>
              <span>状态</span>
              <span>来源</span>
              <span className="text-right">更新时间</span>
            </div>
            {isLoading ? (
              <div className="text-muted-foreground flex h-48 items-center justify-center text-sm">
                正在读取经验台账...
              </div>
            ) : error ? (
              <div className="text-destructive flex h-48 items-center justify-center px-6 text-center text-sm">
                {error instanceof Error ? error.message : "经验台账加载失败"}
              </div>
            ) : records.length === 0 ? (
              <div className="text-muted-foreground flex h-48 items-center justify-center text-sm">
                当前筛选条件下没有已确认经验。
              </div>
            ) : (
              <div className="divide-y">
                {records.map((record) => {
                  const facets = keyFacets(record.facets).slice(0, 4);
                  const futureUse = memoryFutureUseCopy({
                    hasRecord: true,
                    retrievalEnabled: record.retrieval_enabled,
                    decisionDirectiveReady:
                      record.decision_directive !== null &&
                      record.decision_directive !== undefined,
                  });
                  return (
                    <Link
                      key={record.memory_id}
                      href={`/workspace/soc/memory/records/${encodeURIComponent(record.memory_id)}`}
                      className="hover:bg-muted/40 grid min-h-28 gap-3 px-4 py-4 text-sm lg:grid-cols-[minmax(0,1fr)_9rem_10rem_10rem] lg:items-center"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-xs">
                            {record.memory_id} · v{record.version}
                          </span>
                          <Badge variant="outline">{record.memory_type}</Badge>
                        </div>
                        <div className="mt-2 font-semibold break-words">
                          {record.summary}
                        </div>
                        <p className="text-muted-foreground mt-1 line-clamp-2 leading-6">
                          {record.business_lesson?.conclusion ?? record.content}
                        </p>
                        {facets.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {facets.map((facet) => (
                              <Badge key={facet} variant="secondary">
                                {facet}
                              </Badge>
                            ))}
                          </div>
                        ) : null}
                      </div>
                      <div className="flex flex-wrap gap-2 lg:block lg:space-y-2">
                        <Badge variant="outline">
                          {STATUS_LABELS[record.status]}
                        </Badge>
                        <Badge
                          variant={
                            futureUse.tone === "decision"
                              ? "default"
                              : "secondary"
                          }
                        >
                          {futureUse.label}
                        </Badge>
                      </div>
                      <div className="text-muted-foreground min-w-0 text-xs">
                        <div className="truncate" title={recordScope(record)}>
                          {recordScope(record)}
                        </div>
                        <div className="mt-1 truncate font-mono">
                          Alert {record.source.alert_id ?? "-"}
                        </div>
                      </div>
                      <div className="text-muted-foreground text-xs lg:text-right">
                        {formatTime(record.updated_at)}
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
            <div className="flex items-center justify-between border-t px-3 py-2">
              <Button
                size="sm"
                variant="ghost"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeftIcon className="size-4" />
                上一页
              </Button>
              <span className="text-muted-foreground text-xs tabular-nums">
                {records.length === 0 ? 0 : offset + 1}-
                {offset + records.length}
              </span>
              <Button
                size="sm"
                variant="ghost"
                disabled={!page?.has_more}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                下一页
                <ChevronRightIcon className="size-4" />
              </Button>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
