"use client";

import { CheckIcon, ChevronsUpDownIcon, PlusIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  formatCorpusGroupOption,
  summarizeCorpusGroupBehavior,
} from "@/core/soc/corpus-presentation";
import type { SocCorpusWorkbenchGroup } from "@/core/soc/types";

const RESULT_BATCH_SIZE = 50;

export function SocCorpusGroupPicker({
  groups,
  value,
  onValueChange,
}: {
  groups: SocCorpusWorkbenchGroup[];
  value: string;
  onValueChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(RESULT_BATCH_SIZE);
  const selected = groups.find((group) => group.group_id === value);
  const matches = useMemo(() => {
    const terms = search
      .trim()
      .toLocaleLowerCase()
      .split(/\s+/)
      .filter(Boolean);
    return groups.filter((group) => {
      const text = [
        group.group_id,
        group.rule_name,
        group.rule_code,
        group.source_type,
        group.detection_key,
        ...group.behavior_components,
        ...group.behavior_components.map((component) =>
          summarizeCorpusGroupBehavior([component]),
        ),
      ]
        .join(" ")
        .toLocaleLowerCase();
      return terms.every((term) => text.includes(term));
    });
  }, [groups, search]);
  const select = (groupId: string) => {
    onValueChange(groupId);
    setOpen(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setSearch("");
          setLimit(RESULT_BATCH_SIZE);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button
          id="corpus-group-filter"
          variant="outline"
          className="w-full min-w-0 justify-between font-normal"
          title={
            selected ? formatCorpusGroupOption(selected) : "全部行为模式组"
          }
        >
          <span className="truncate">
            {selected ? formatCorpusGroupOption(selected) : "全部行为模式组"}
          </span>
          <ChevronsUpDownIcon className="size-4 shrink-0 opacity-50" />
        </Button>
      </DialogTrigger>
      <DialogContent
        className="gap-0 overflow-hidden p-0 sm:max-w-2xl"
        aria-describedby={undefined}
      >
        <DialogHeader className="px-4 py-4">
          <DialogTitle>查找行为模式组</DialogTitle>
        </DialogHeader>
        <Command shouldFilter={false}>
          <CommandInput
            aria-label="搜索分组"
            placeholder="规则名称 / 规则编码 / 行为关键词 / 分组编号"
            value={search}
            onValueChange={(next) => {
              setSearch(next);
              setLimit(RESULT_BATCH_SIZE);
            }}
          />
          <div
            className="text-muted-foreground border-b px-4 py-2 text-xs"
            role="status"
          >
            {search.trim()
              ? `找到 ${matches.length} 个分组`
              : `共 ${groups.length} 个分组`}
          </div>
          <CommandList
            className="max-h-[min(60vh,480px)] p-1"
            aria-label="行为模式组列表"
          >
            {!search.trim() && (
              <CommandItem value="all" onSelect={() => select("all")}>
                <span className="flex-1">全部行为模式组</span>
                {value === "all" && <CheckIcon className="size-4" />}
              </CommandItem>
            )}
            <CommandEmpty>未找到匹配的分组</CommandEmpty>
            {matches.slice(0, limit).map((group) => (
              <CommandItem
                key={group.group_id}
                value={group.group_id}
                aria-label={formatCorpusGroupOption(group)}
                onSelect={() => select(group.group_id)}
                className="items-start border-b px-3 py-3 last:border-0"
              >
                <div className="min-w-0 flex-1 space-y-1 break-words">
                  <p className="font-medium">
                    {group.rule_name ?? group.rule_code ?? "未命名规则"}
                  </p>
                  <p className="text-muted-foreground text-xs">
                    {summarizeCorpusGroupBehavior(group.behavior_components) ||
                      "暂无行为摘要"}
                  </p>
                  <p className="text-muted-foreground text-xs break-all">
                    {group.rule_code ?? group.detection_key ?? "无规则编码"} ·{" "}
                    {group.source_type.toUpperCase()} · {group.group_id}
                  </p>
                </div>
                <span className="shrink-0 text-xs tabular-nums">
                  {group.alert_count} 条
                </span>
                {value === group.group_id && <CheckIcon className="size-4" />}
              </CommandItem>
            ))}
            {matches.length > limit && (
              <CommandItem
                value="show-more"
                onSelect={() =>
                  setLimit((current) => current + RESULT_BATCH_SIZE)
                }
              >
                <PlusIcon className="size-4" />
                显示更多分组（剩余 {matches.length - limit} 组）
              </CommandItem>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
