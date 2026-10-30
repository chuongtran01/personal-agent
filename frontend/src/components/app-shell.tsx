"use client";

import { Menu } from "lucide-react";
import { Suspense, type ReactNode } from "react";

import { AppSidebar } from "@/components/app-sidebar";
import { Button } from "@/components/ui/button";
import { SidebarInset, SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * On a narrow screen the sidebar is a drawer that is out of sight, so the page needs its own button
 * to open it. On a wide screen the sidebar carries its own toggle and the page has no bar at all.
 */
function PhoneBar() {
  const { isMobile, toggleSidebar } = useSidebar();
  if (!isMobile) return null;
  return (
    <header className="flex h-12 shrink-0 items-center px-3">
      <Button variant="ghost" size="icon" aria-label="Open sidebar" onClick={toggleSidebar}>
        <Menu aria-hidden />
      </Button>
    </header>
  );
}

/** The frame around the signed-in pages: the sidebar beside them (a drawer on a narrow screen). */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    // The folded sidebar names its icons in tooltips, which need a provider above them
    <TooltipProvider>
      <SidebarProvider className="h-svh">
        {/* The open chat comes from the address, which is runtime data */}
        <Suspense fallback={null}>
          <AppSidebar />
        </Suspense>
        <SidebarInset className="h-svh min-h-0">
          <PhoneBar />
          <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  );
}
