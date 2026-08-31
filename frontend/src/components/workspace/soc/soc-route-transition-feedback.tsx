"use client";

import { usePathname } from "next/navigation";
import {
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  useEffect,
  useState,
} from "react";
import { flushSync } from "react-dom";

interface PendingSocRoute {
  label: string;
  pathname: string;
  startedAt: number;
}

const MINIMUM_FEEDBACK_MS = 900;
const FAILED_NAVIGATION_TIMEOUT_MS = 15_000;

export function SocRouteTransitionFeedback({
  children,
}: {
  children: ReactNode;
}) {
  const pathname = usePathname();
  const [pendingRoute, setPendingRoute] = useState<PendingSocRoute | null>(
    null,
  );

  useEffect(() => {
    if (!pendingRoute) return;
    const reachedTarget = pathname === pendingRoute.pathname;
    const elapsed = performance.now() - pendingRoute.startedAt;
    const delay = reachedTarget
      ? Math.max(0, MINIMUM_FEEDBACK_MS - elapsed)
      : FAILED_NAVIGATION_TIMEOUT_MS;
    const timeout = window.setTimeout(() => setPendingRoute(null), delay);
    return () => window.clearTimeout(timeout);
  }, [pathname, pendingRoute]);

  const handleClickCapture = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    const target = event.target;
    if (!(target instanceof Element)) return;
    const anchor = target.closest<HTMLAnchorElement>("a[href]");
    if (
      !anchor ||
      anchor.target === "_blank" ||
      anchor.hasAttribute("download")
    ) {
      return;
    }
    const destination = new URL(anchor.href, window.location.href);
    if (
      destination.origin !== window.location.origin ||
      !destination.pathname.startsWith("/workspace/soc/") ||
      destination.pathname === pathname
    ) {
      return;
    }
    const primaryLabel = anchor.querySelector("span")?.textContent?.trim();
    const fallbackLabel = anchor.textContent?.replace(/\s+/g, " ").trim();
    const label = primaryLabel?.length
      ? primaryLabel
      : fallbackLabel?.length
        ? fallbackLabel
        : "SOC 页面";
    flushSync(() => {
      setPendingRoute({
        label,
        pathname: destination.pathname,
        startedAt: performance.now(),
      });
    });
  };

  return (
    <div className="contents" onClickCapture={handleClickCapture}>
      {pendingRoute ? (
        <div
          className="fixed inset-x-0 top-0 z-[100] h-1 overflow-hidden bg-sky-100"
          role="status"
          aria-live="polite"
          aria-label={`正在打开${pendingRoute.label}`}
        >
          <div className="h-full w-2/3 animate-pulse bg-sky-600" />
        </div>
      ) : null}
      {children}
    </div>
  );
}
