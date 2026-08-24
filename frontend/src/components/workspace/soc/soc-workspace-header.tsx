"use client";

import {
  ActivityIcon,
  BrainCircuitIcon,
  FileSearchIcon,
  ShieldCheckIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

const NAVIGATION: {
  href: string;
  activePrefix?: string;
  label: string;
  icon: LucideIcon;
  section: "operations" | "validation";
}[] = [
  {
    href: "/workspace/soc/operations",
    label: "运营总览",
    icon: ActivityIcon,
    section: "operations",
  },
  {
    href: "/workspace/soc/review/alerts",
    activePrefix: "/workspace/soc/review",
    label: "审核中心",
    icon: ShieldCheckIcon,
    section: "operations",
  },
  {
    href: "/workspace/soc/memory",
    label: "Memory Center",
    icon: BrainCircuitIcon,
    section: "operations",
  },
  {
    href: "/workspace/soc/normalization",
    label: "归一化运维",
    icon: WrenchIcon,
    section: "operations",
  },
  {
    href: "/workspace/soc/corpus-validation",
    label: "语料验证",
    icon: FileSearchIcon,
    section: "validation",
  },
];

export function SocWorkspaceHeader({
  icon: Icon,
  title,
  description,
  actions,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  const pathname = usePathname();
  const navigationScrollerRef = useRef<HTMLDivElement>(null);
  const activeLinkRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const scroller = navigationScrollerRef.current;
    const activeLink = activeLinkRef.current;
    if (!scroller || !activeLink) {
      return;
    }
    let frame: number | null = null;
    const alignActiveLink = () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        if (scroller.scrollWidth <= scroller.clientWidth) {
          scroller.scrollLeft = 0;
          return;
        }
        const scrollerRect = scroller.getBoundingClientRect();
        const activeLinkRect = activeLink.getBoundingClientRect();
        const activeCenter =
          activeLinkRect.left -
          scrollerRect.left +
          scroller.scrollLeft +
          activeLinkRect.width / 2;
        scroller.scrollTo({
          left: Math.max(0, activeCenter - scroller.clientWidth / 2),
          behavior: "auto",
        });
      });
    };
    const observer = new ResizeObserver(alignActiveLink);
    observer.observe(scroller);
    alignActiveLink();
    return () => {
      observer.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [pathname]);

  return (
    <header className="bg-background shrink-0 border-b">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 md:px-7">
        <div className="flex min-w-0 items-center gap-3">
          <Icon className="size-5 shrink-0" />
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold">{title}</h1>
            <p className="text-muted-foreground mt-0.5 text-sm">
              {description}
            </p>
          </div>
        </div>
        {actions ? (
          <div className="flex w-full flex-wrap items-center gap-2 border-t pt-3 lg:w-auto lg:justify-end lg:border-t-0 lg:border-l lg:pt-0 lg:pl-4">
            {actions}
          </div>
        ) : null}
      </div>

      <div
        ref={navigationScrollerRef}
        className="overflow-x-auto border-t"
        data-testid="soc-workspace-nav"
      >
        <nav
          aria-label="SOC 运营导航"
          className="flex min-w-max items-stretch px-3 md:px-5"
        >
          {NAVIGATION.map((item, index) => {
            const active =
              pathname === item.href ||
              pathname.startsWith(`${item.activePrefix ?? item.href}/`);
            const ItemIcon = item.icon;
            const beginsValidation =
              item.section === "validation" &&
              NAVIGATION[index - 1]?.section !== "validation";
            return (
              <div
                key={item.href}
                className={cn(
                  "flex items-stretch",
                  beginsValidation && "ml-3 border-l pl-3",
                )}
              >
                <Link
                  ref={active ? activeLinkRef : undefined}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "text-muted-foreground hover:text-foreground flex h-11 items-center gap-2 border-b-2 border-transparent px-3 text-sm transition-colors",
                    active && "border-foreground text-foreground font-medium",
                  )}
                >
                  <ItemIcon className="size-4" />
                  <span>{item.label}</span>
                  {item.section === "validation" ? (
                    <span className="border-muted-foreground/40 text-muted-foreground border px-1 py-0.5 text-[10px] leading-none">
                      DEV
                    </span>
                  ) : null}
                </Link>
              </div>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
