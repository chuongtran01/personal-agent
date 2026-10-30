"use client";

import { Suspense, type ReactNode } from "react";

import { AppSidebar } from "@/components/app-sidebar";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";

/** The frame around the signed-in pages: the sidebar beside them (a drawer on a narrow screen). */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SidebarProvider className="h-svh">
      {/* The open chat comes from the address, which is runtime data */}
      <Suspense fallback={null}>
        <AppSidebar />
      </Suspense>
      <SidebarInset className="h-svh min-h-0">
        <header className="flex h-12 shrink-0 items-center px-3">
          <SidebarTrigger />
        </header>
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  );
}
