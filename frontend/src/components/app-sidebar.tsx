"use client";

import { SquarePen, User } from "lucide-react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";

import { logout } from "@/api/auth";
import { ChatList } from "@/components/chat-list";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { resetStore } from "@/lib/chat-store";

/** The app name, a New chat entry, the recent chats, and who is signed in. */
export function AppSidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const { setOpenMobile } = useSidebar();

  return (
    <Sidebar>
      <SidebarHeader className="px-4 pt-4">
        <span className="text-base font-semibold">Personal Agent</span>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={pathname === "/"}>
                <Link href="/" onClick={() => setOpenMobile(false)}>
                  <SquarePen />
                  <span>New chat</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
        <ChatList />
      </SidebarContent>
      <SidebarFooter>
        <div className="flex items-center gap-2 px-1">
          <Avatar className="size-8">
            <AvatarFallback>
              <User className="size-4" aria-hidden />
            </AvatarFallback>
          </Avatar>
          <span className="flex-1 truncate text-sm font-medium">Owner</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              resetStore(); // the next owner must not see this chat's text
              logout();
              router.replace("/login");
            }}
          >
            Log out
          </Button>
        </div>
      </SidebarFooter>
    </Sidebar>
  );
}
