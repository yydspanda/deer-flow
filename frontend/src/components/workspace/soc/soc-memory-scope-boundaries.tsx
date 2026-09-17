"use client";

import { RotateCcwIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useSocMemoryScopeBoundaries,
  useReleaseSocMemoryScopeBoundary,
} from "@/core/soc/hooks";

export function SocMemoryScopeBoundaries({ memoryId }: { memoryId: string }) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const { data, error, isLoading } = useSocMemoryScopeBoundaries(
    memoryId,
    open,
  );
  const mutation = useReleaseSocMemoryScopeBoundary();
  return (
    <details
      className="mt-4 border-t pt-3"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="w-fit cursor-pointer text-sm font-medium">
        细分经验与范围管理
      </summary>
      {isLoading && (
        <p className="mt-2 text-sm" role="status">
          正在读取...
        </p>
      )}
      {(error ?? mutation.error) && (
        <p role="alert" className="text-destructive mt-2 text-sm">
          {(error ?? mutation.error)?.message}
        </p>
      )}
      {data && !data.exceptions.length && (
        <p className="text-muted-foreground mt-2 text-sm">
          当前没有已经确认的细分范围。
        </p>
      )}
      {data?.exceptions.map((item) => (
        <div key={item.memory_id} className="space-y-2 border-b py-3 text-sm">
          <Link
            className="font-medium underline underline-offset-4"
            href={`/workspace/soc/memory/records/${item.memory_id}`}
          >
            {item.summary}
          </Link>
          <p className="text-muted-foreground">
            {item.active
              ? "符合该范围时优先使用细分经验。"
              : item.released
                ? "已明确恢复由本经验处理。"
                : "细分经验暂停或过期，该范围继续正常研判，不自动退回本经验。"}
          </p>
          {data.active &&
            !item.active &&
            !item.released &&
            (target === item.memory_id ? (
              <div className="space-y-2">
                <Input
                  aria-label="恢复范围的业务依据"
                  placeholder="确认这部分告警可以再次沿用原经验的依据"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    disabled={!reason.trim() || mutation.isPending}
                    onClick={async () => {
                      try {
                        await mutation.mutateAsync({
                          memory_id: memoryId,
                          expected_version: data.version,
                          exception_memory_id: item.memory_id,
                          expected_exception_version: item.version,
                          reason: reason.trim(),
                        });
                        setTarget(null);
                        setReason("");
                      } catch {
                        /* Keep the confirmation and error visible. */
                      }
                    }}
                  >
                    <RotateCcwIcon className="size-4" />
                    确认恢复原经验处理
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setTarget(null)}
                  >
                    取消
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  setTarget(item.memory_id);
                  setReason("");
                }}
              >
                <RotateCcwIcon className="size-4" />
                恢复这部分范围
              </Button>
            ))}
        </div>
      ))}
    </details>
  );
}
