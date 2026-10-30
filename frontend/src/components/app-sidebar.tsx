"use client";

import { Brain, ChevronsUpDown, LogOut, MessageSquare, SquarePen, User } from "lucide-react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";

import { logout } from "@/api/auth";
import { ChatList } from "@/components/chat-list";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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

/**
 * The app name, a New chat entry, the recent chats, and who is signed in. Folded, it stays as a
 * narrow strip of icons (the mark, New chat, Chats, the account) and the page keeps its place.
 */
export function AppSidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const { setOpen, setOpenMobile } = useSidebar();

  function signOut() {
    resetStore(); // the next owner must not see this chat's text
    logout();
    router.replace("/login");
  }

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-3 pt-4 group-data-[collapsible=icon]:px-2">
        <div className="flex items-center gap-2 px-1 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
          <Brain className="size-5 shrink-0" aria-hidden />
          <span className="text-base font-semibold group-data-[collapsible=icon]:hidden">
            Personal Agent
          </span>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={pathname === "/"} tooltip="New chat">
                <Link href="/" onClick={() => setOpenMobile(false)}>
                  <SquarePen />
                  <span>New chat</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
            {/* Only in the folded strip, where the list of chats is out of sight: it unfolds the sidebar */}
            <SidebarMenuItem className="hidden group-data-[collapsible=icon]:block">
              <SidebarMenuButton tooltip="Chats" onClick={() => setOpen(true)}>
                <MessageSquare />
                <span>Chats</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
        <ChatList />
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
                  <Avatar className="size-8">
                    <AvatarFallback>
                      <User className="size-4" aria-hidden />
                    </AvatarFallback>
                  </Avatar>
                  <span className="flex-1 truncate text-sm font-medium">Owner</span>
                  <ChevronsUpDown className="ml-auto size-4 text-muted-foreground" aria-hidden />
                </SidebarMenuButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="top" align="start" className="w-(--radix-dropdown-menu-trigger-width) min-w-56">
                <DropdownMenuLabel>Owner</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={signOut}>
                  <LogOut aria-hidden />
                  Log out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}
