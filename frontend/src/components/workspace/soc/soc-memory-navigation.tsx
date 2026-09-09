import {
  BookOpenCheckIcon,
  ClipboardCheckIcon,
  Layers3Icon,
} from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";

const VIEWS = [
  {
    href: "/workspace/soc/memory",
    label: "已确认经验",
    icon: BookOpenCheckIcon,
  },
  {
    href: "/workspace/soc/review/memory-candidates",
    label: "经验审核",
    icon: ClipboardCheckIcon,
  },
  {
    href: "/workspace/soc/memory/patterns",
    label: "同类告警积累",
    icon: Layers3Icon,
  },
];

export function SocMemoryNavigation({ pathname }: { pathname: string }) {
  const owns = (prefix: string) =>
    pathname === prefix || pathname.startsWith(`${prefix}/`);
  if (
    !owns("/workspace/soc/memory") &&
    !owns("/workspace/soc/review/memory-candidates")
  )
    return null;
  const activeHref = owns("/workspace/soc/review/memory-candidates")
    ? "/workspace/soc/review/memory-candidates"
    : owns("/workspace/soc/memory/patterns")
      ? "/workspace/soc/memory/patterns"
      : "/workspace/soc/memory";
  return (
    <nav aria-label="经验中心视图" className="border-t px-3 md:px-5">
      <div className="grid max-w-2xl grid-cols-3">
        {VIEWS.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            aria-current={href === activeHref ? "page" : undefined}
            className={cn(
              "text-muted-foreground hover:bg-muted/50 hover:text-foreground flex min-h-11 min-w-0 items-center justify-center gap-1 border-b-2 border-transparent px-2 py-2 text-xs sm:gap-2 sm:text-sm",
              href === activeHref &&
                "border-foreground text-foreground font-semibold",
            )}
          >
            <Icon className="size-3.5 shrink-0 sm:size-4" />
            <span>{label}</span>
          </Link>
        ))}
      </div>
    </nav>
  );
}
