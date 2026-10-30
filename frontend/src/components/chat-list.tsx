"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  useSidebar,
} from "@/components/ui/sidebar";
import { refreshChatList } from "@/lib/chat-store";
import { relativeTime } from "@/lib/time";
import { useChatList } from "@/lib/use-chat";

/** The "Recents" group of the sidebar: the chats, newest first. Must sit inside a SidebarProvider. */
export function ChatList() {
  const { items, error } = useChatList();
  const pathname = usePathname();
  const { setOpenMobile } = useSidebar();

  useEffect(() => {
    void refreshChatList();
  }, []);

  return (
    <SidebarGroup>
      <SidebarGroupLabel>Recents</SidebarGroupLabel>
      <SidebarGroupContent>
        {items === null && !error && (
          <div className="flex flex-col gap-1" aria-hidden>
            <SidebarMenuSkeleton />
            <SidebarMenuSkeleton />
            <SidebarMenuSkeleton />
          </div>
        )}
        {error && (
          <div className="flex flex-col items-start gap-2 px-2 text-sm">
            <span>Could not load your chats.</span>
            <Button size="sm" variant="outline" onClick={() => void refreshChatList()}>
              Try again
            </Button>
          </div>
        )}
        {items?.length === 0 && (
          <p className="px-2 text-sm text-muted-foreground">No chats yet.</p>
        )}
        <SidebarMenu aria-label="Recent chats">
          {items?.map((chat) => {
            const href = `/chats/${encodeURIComponent(chat.id)}`;
            return (
              <SidebarMenuItem key={chat.id}>
                <SidebarMenuButton asChild isActive={pathname === href}>
                  <Link
                    href={href}
                    title={relativeTime(chat.created_at)}
                    aria-current={pathname === href ? "page" : undefined}
                    onClick={() => setOpenMobile(false)}
                  >
                    <span className="truncate">{chat.preview ?? "New chat"}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
