"use client";

import {
  Brain,
  ChevronsUpDown,
  Library,
  LogOut,
  MessageSquare,
  PanelLeft,
  SquarePen,
  User,
} from "lucide-react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";

import { logout } from "@/api/auth";
import { ChatList } from "@/components/chat-list";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { resetStore } from "@/lib/chat-store";

/**
 * The app name, a New chat entry, the recent chats, and who is signed in. Folded, it stays as a
 * narrow strip of icons (the mark, New chat, Chats, the account) and the page keeps its place.
 */
export function AppSidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const { isMobile, state, setOpen, setOpenMobile, toggleSidebar } = useSidebar();
  const folded = state === "collapsed" && !isMobile;

  function signOut() {
    resetStore(); // the next owner must not see this chat's text
    logout();
    router.replace("/login");
  }

  const closeButton = (
    <Button
      variant="ghost"
      size="icon"
      className="ml-auto size-8"
      aria-label="Close sidebar"
      onClick={toggleSidebar}
    >
      <PanelLeft aria-hidden />
    </Button>
  );

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-3 pt-3 group-data-[collapsible=icon]:px-2">
        {folded ? (
          // Folded, the mark is the way back: on hover or focus it turns into the unfold button
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label="Open sidebar"
                onClick={toggleSidebar}
                className="group/logo mx-auto flex size-8 items-center justify-center rounded-md text-sidebar-foreground outline-hidden hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-sidebar-ring"
              >
                <Brain className="size-5 group-hover/logo:hidden group-focus-visible/logo:hidden" aria-hidden />
                <PanelLeft className="hidden size-5 group-hover/logo:block group-focus-visible/logo:block" aria-hidden />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">Open sidebar</TooltipContent>
          </Tooltip>
        ) : (
          <div className="flex items-center gap-2 pl-1">
            <Brain className="size-5 shrink-0" aria-hidden />
            <span className="text-base font-semibold">Personal Agent</span>
            {isMobile ? (
              // In the drawer focus lands on this button when it opens, and a tooltip appearing on
              // focus would take the first Escape, so closing the drawer would need two presses
              closeButton
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>{closeButton}</TooltipTrigger>
                <TooltipContent side="bottom">Close sidebar</TooltipContent>
              </Tooltip>
            )}
          </div>
        )}
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
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={pathname === "/memories"} tooltip="Memories">
                <Link href="/memories" onClick={() => setOpenMobile(false)}>
                  <Library />
                  <span>Memories</span>
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
                <DropdownMenuItem asChild>
                  <Link href="/memories" onClick={() => setOpenMobile(false)}>
                    <Library aria-hidden />
                    Memories
                  </Link>
                </DropdownMenuItem>
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
