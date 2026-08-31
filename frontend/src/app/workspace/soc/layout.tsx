import type { ReactNode } from "react";

import { SocRouteTransitionFeedback } from "@/components/workspace/soc/soc-route-transition-feedback";

export default function SocWorkspaceLayout({
  children,
}: {
  children: ReactNode;
}) {
  return <SocRouteTransitionFeedback>{children}</SocRouteTransitionFeedback>;
}
