"use client";

import { ActivityIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";

export function SocNavigationItem() {
  const { t } = useI18n();
  const pathname = usePathname();
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        isActive={pathname.startsWith("/workspace/soc")}
        asChild
      >
        <Link
          className="text-muted-foreground"
          href="/workspace/soc/operations"
        >
          <ActivityIcon />
          <span>{t.sidebar.socOperations}</span>
        </Link>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}
