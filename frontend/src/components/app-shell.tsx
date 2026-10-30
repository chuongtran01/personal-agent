"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { logout } from "@/api/auth";
import { Button } from "@/components/ui/button";

/** The frame around the signed-in pages. The chat list and menu join it as they are built. */
export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();

  return (
    <div className="flex min-h-svh flex-col">
      <header className="flex items-center justify-between border-b px-4 py-2">
        <span className="font-medium">Personal Agent</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            logout();
            router.replace("/login");
          }}
        >
          Log out
        </Button>
      </header>
      <div className="flex flex-1 flex-col">{children}</div>
    </div>
  );
}
