"use client";

import {
  ChevronLeftIcon,
  ChevronRightIcon,
  PlusIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSocMemoryScopeOptions } from "@/core/soc/hooks";

const labels: Record<string, string> = {
  destination: "目标 IP",
  source: "来源 IP",
  ip: "IP（未区分角色）",
  host: "主机",
  user: "账号",
  account: "账号",
  asset: "资产组",
  domain: "域名",
  attacker: "攻击者",
  victim: "受害者",
};

export function SocMemoryEntityLimits({
  candidateId,
  selected,
  onChange,
  disabled,
}: {
  candidateId: string;
  selected: Record<string, string[]>;
  onChange: (values: Record<string, string[]>) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [dimension, setDimension] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search);
      setOffset(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);
  const [key, prefix] = dimension.split("/");
  const selectedInDimension = (selected[key ?? ""] ?? []).filter((value) =>
    value.startsWith(`${prefix}:`),
  ).length;
  const { data, isFetching, error, refetch } = useSocMemoryScopeOptions(
    candidateId,
    {
      facet_key: key === "" ? undefined : key,
      prefix,
      search: query,
      offset,
    },
    open,
  );

  return (
    <div className="mt-3" data-memory-entity-limits>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        {open ? <XIcon className="size-4" /> : <PlusIcon className="size-4" />}
        {open ? "收起可选限制" : "添加适用范围限制"}
      </Button>
      {open && (
        <div className="mt-3 space-y-3 border-l-2 pl-3">
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="限制维度"
              value={dimension}
              className="bg-background h-9 max-w-full rounded-md border px-2 text-sm"
              onChange={(event) => {
                setDimension(event.target.value);
                setOffset(0);
                setSearch("");
              }}
            >
              <option value="">选择限制维度</option>
              {data?.groups.map((group) => (
                <option
                  key={`${group.facet_key}/${group.value_prefix}`}
                  value={`${group.facet_key}/${group.value_prefix}`}
                >
                  {labels[group.value_prefix] ?? group.value_prefix}
                </option>
              ))}
            </select>
            {dimension && (
              <div className="relative min-w-40 flex-1">
                <SearchIcon className="text-muted-foreground absolute top-2.5 left-2 size-4" />
                <Input
                  className="pl-8"
                  aria-label="搜索来源样本实体"
                  value={search}
                  placeholder="搜索 IP、主机或账号"
                  onChange={(event) => setSearch(event.target.value)}
                />
              </div>
            )}
          </div>
          {error ? (
            <p role="alert" className="text-destructive text-sm">
              加载失败。
              <button
                type="button"
                className="underline"
                onClick={() => void refetch()}
              >
                重试
              </button>
            </p>
          ) : isFetching ? (
            <p role="status" className="text-muted-foreground text-sm">
              正在读取来源样本...
            </p>
          ) : (
            dimension && (
              <>
                <div className="divide-y">
                  {data?.items.map((item) => (
                    <label
                      key={`${item.facet_key}/${item.value}`}
                      className="flex min-w-0 items-start gap-2 py-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        className="mt-1 size-4 shrink-0 accent-emerald-700"
                        disabled={
                          disabled ||
                          (selectedInDimension >= 20 &&
                            !selected[item.facet_key]?.includes(item.value))
                        }
                        checked={
                          selected[item.facet_key]?.includes(item.value) ??
                          false
                        }
                        onChange={(event) => {
                          const values = selected[item.facet_key] ?? [];
                          const next = {
                            ...selected,
                            [item.facet_key]: event.target.checked
                              ? [...new Set([...values, item.value])]
                              : values.filter((value) => value !== item.value),
                          };
                          if (!next[item.facet_key]?.length)
                            delete next[item.facet_key];
                          onChange(next);
                        }}
                      />
                      <span className="min-w-0 flex-1 break-all">
                        {item.value.slice(item.value.indexOf(":") + 1)}
                      </span>
                      <span className="text-muted-foreground shrink-0 text-xs leading-6">
                        {item.from_current_alert ? "当前来源 · " : ""}
                        {item.sample_count} 条样本
                      </span>
                    </label>
                  ))}
                  {data?.total === 0 && (
                    <p className="text-muted-foreground py-2 text-sm">
                      没有符合搜索条件的实体。
                    </p>
                  )}
                </div>
                {selectedInDimension >= 20 && (
                  <p role="status" className="text-muted-foreground text-sm">
                    此类限制已选满 20 个值。
                  </p>
                )}
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground">
                    共 {data?.total ?? 0} 个值 · 来源{" "}
                    {data?.source_sample_count ?? 0} 条样本
                  </span>
                  <div className="flex gap-1">
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      title="上一页"
                      aria-label="上一页"
                      disabled={!offset}
                      onClick={() => setOffset(Math.max(0, offset - 10))}
                    >
                      <ChevronLeftIcon className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      title="下一页"
                      aria-label="下一页"
                      disabled={offset + 10 >= (data?.total ?? 0)}
                      onClick={() => setOffset(offset + 10)}
                    >
                      <ChevronRightIcon className="size-4" />
                    </Button>
                  </div>
                </div>
              </>
            )
          )}
        </div>
      )}
    </div>
  );
}
