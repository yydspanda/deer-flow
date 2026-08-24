"use client";

import CodeMirror, {
  EditorView,
  type ReactCodeMirrorRef,
} from "@uiw/react-codemirror";
import {
  ChevronDownIcon,
  ChevronUpIcon,
  ClipboardIcon,
  DownloadIcon,
  FileJsonIcon,
  Maximize2Icon,
  Minimize2Icon,
  SearchIcon,
  WrapTextIcon,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Toggle } from "@/components/ui/toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { loadCodeEditorExtensions } from "@/components/workspace/code-editor-extensions";
import { cn } from "@/lib/utils";

type JsonLayout = "formatted" | "compact";

function stringifyJson(value: unknown, layout: JsonLayout) {
  return (
    JSON.stringify(value, null, layout === "formatted" ? 2 : undefined) ??
    String(value)
  );
}

function downloadJson(fileName: string, value: unknown) {
  const blob = new Blob([stringifyJson(value, "formatted")], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}

function findMatches(value: string, query: string) {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  if (!normalizedQuery) return [];

  const haystack = value.toLocaleLowerCase();
  const offsets: number[] = [];
  let cursor = 0;
  while (offsets.length < 5_000) {
    const offset = haystack.indexOf(normalizedQuery, cursor);
    if (offset < 0) break;
    offsets.push(offset);
    cursor = offset + Math.max(normalizedQuery.length, 1);
  }
  return offsets;
}

export function SocJsonCodeViewer({
  fileName,
  value,
}: {
  fileName: string;
  value: unknown;
}) {
  const { resolvedTheme } = useTheme();
  const editorRef = useRef<ReactCodeMirrorRef>(null);
  const [layout, setLayout] = useState<JsonLayout>("formatted");
  const [expanded, setExpanded] = useState(false);
  const [wrapLines, setWrapLines] = useState(false);
  const [query, setQuery] = useState("");
  const [matchIndex, setMatchIndex] = useState(0);
  const [loaded, setLoaded] = useState<
    Awaited<ReturnType<typeof loadCodeEditorExtensions>> | undefined
  >();

  const json = useMemo(() => stringifyJson(value, layout), [layout, value]);
  const matches = useMemo(() => findMatches(json, query), [json, query]);
  const extensions = useMemo(
    () =>
      loaded
        ? [
            ...loaded.extensions,
            ...(wrapLines ? [EditorView.lineWrapping] : []),
          ]
        : [],
    [loaded, wrapLines],
  );

  useEffect(() => {
    let cancelled = false;
    setLoaded(undefined);
    void loadCodeEditorExtensions(
      "json",
      resolvedTheme === "dark" ? "dark" : "light",
    ).then((next) => {
      if (!cancelled) setLoaded(next);
    });
    return () => {
      cancelled = true;
    };
  }, [resolvedTheme]);

  useEffect(() => {
    if (!matches.length) return;
    const normalizedIndex = matchIndex % matches.length;
    const from = matches[normalizedIndex]!;
    const to = from + query.trim().length;
    const view = editorRef.current?.view;
    if (!view) return;
    view.dispatch({
      selection: { anchor: from, head: to },
      effects: EditorView.scrollIntoView(from, { y: "center" }),
    });
  }, [json, matchIndex, matches, query]);

  const moveMatch = (direction: 1 | -1) => {
    if (!matches.length) return;
    setMatchIndex(
      (current) => (current + direction + matches.length) % matches.length,
    );
  };

  const byteCount = useMemo(
    () => new TextEncoder().encode(json).byteLength,
    [json],
  );
  const lineCount = useMemo(() => json.split("\n").length, [json]);

  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2 border-y bg-zinc-50 px-4 py-2.5 dark:bg-zinc-900">
        <div className="flex min-w-0 items-center gap-2">
          <FileJsonIcon className="size-4 shrink-0" />
          <span className="truncate font-mono text-xs">{fileName}</span>
          <span className="text-muted-foreground hidden text-xs tabular-nums sm:inline">
            {lineCount.toLocaleString()} lines · {byteCount.toLocaleString()}{" "}
            bytes
          </span>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <ToggleGroup
            type="single"
            value={layout}
            variant="outline"
            size="sm"
            onValueChange={(next) => {
              if (next === "formatted" || next === "compact") {
                setLayout(next);
                setMatchIndex(0);
              }
            }}
            aria-label="JSON 显示格式"
          >
            <ToggleGroupItem value="formatted">格式化</ToggleGroupItem>
            <ToggleGroupItem value="compact">紧凑</ToggleGroupItem>
          </ToggleGroup>
          <Toggle
            pressed={wrapLines}
            variant="outline"
            size="sm"
            title="切换长行换行"
            aria-label="切换长行换行"
            onPressedChange={setWrapLines}
          >
            <WrapTextIcon className="size-4" />
          </Toggle>
          <Button
            variant="outline"
            size="icon-sm"
            title={expanded ? "恢复标准高度" : "扩大浏览区域"}
            aria-label={expanded ? "恢复标准高度" : "扩大浏览区域"}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? (
              <Minimize2Icon className="size-4" />
            ) : (
              <Maximize2Icon className="size-4" />
            )}
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            title="复制格式化 JSON"
            aria-label="复制格式化 JSON"
            onClick={() => {
              void navigator.clipboard
                .writeText(stringifyJson(value, "formatted"))
                .then(() => toast.success(`已复制 ${fileName}`))
                .catch(() => toast.error("复制失败"));
            }}
          >
            <ClipboardIcon className="size-4" />
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            title="下载格式化 JSON"
            aria-label="下载格式化 JSON"
            onClick={() => downloadJson(fileName, value)}
          >
            <DownloadIcon className="size-4" />
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b bg-white px-4 py-2 dark:bg-zinc-950">
        <div className="relative min-w-[220px] flex-1 sm:max-w-md">
          <SearchIcon className="text-muted-foreground pointer-events-none absolute top-2.5 left-3 size-4" />
          <Input
            value={query}
            className="h-9 pl-9 font-mono text-xs"
            placeholder="搜索 key、value 或来源路径"
            aria-label="搜索 JSON"
            onChange={(event) => {
              setQuery(event.target.value);
              setMatchIndex(0);
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              moveMatch(event.shiftKey ? -1 : 1);
            }}
          />
        </div>
        <span className="text-muted-foreground min-w-16 text-center font-mono text-xs tabular-nums">
          {query.trim()
            ? matches.length
              ? `${matchIndex + 1} / ${matches.length}${matches.length === 5_000 ? "+" : ""}`
              : "0 / 0"
            : "—"}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          title="上一个匹配"
          aria-label="上一个匹配"
          disabled={!matches.length}
          onClick={() => moveMatch(-1)}
        >
          <ChevronUpIcon className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          title="下一个匹配"
          aria-label="下一个匹配"
          disabled={!matches.length}
          onClick={() => moveMatch(1)}
        >
          <ChevronDownIcon className="size-4" />
        </Button>
      </div>

      <div
        className={cn(
          "overflow-hidden bg-zinc-950",
          "[&_.cm-editor]:text-xs [&_.cm-focused]:outline-none",
          "[&_.cm-gutters]:border-r [&_.cm-gutters]:border-zinc-800",
        )}
      >
        {loaded ? (
          <CodeMirror
            ref={editorRef}
            value={json}
            height={expanded ? "72vh" : "clamp(420px, 58vh, 680px)"}
            readOnly
            editable={false}
            theme={loaded.theme}
            extensions={extensions}
            aria-label={`${fileName} JSON 浏览器`}
            basicSetup={{
              bracketMatching: true,
              closeBrackets: false,
              foldGutter: true,
              highlightActiveLine: true,
              highlightActiveLineGutter: true,
              lineNumbers: true,
              searchKeymap: true,
            }}
          />
        ) : (
          <div className="flex h-[420px] items-center justify-center font-mono text-xs text-zinc-400">
            Loading JSON viewer…
          </div>
        )}
      </div>
    </div>
  );
}
