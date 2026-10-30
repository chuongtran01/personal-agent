"use client";

import { Menu } from "lucide-react";
import { useRouter } from "next/navigation";
import { Suspense, useState, type ReactNode } from "react";

import { logout } from "@/api/auth";
import { ChatList } from "@/components/chat-list";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { resetStore } from "@/lib/chat-store";

/** The frame around the signed-in pages: the chat list beside them, or in a drawer on a narrow screen. */
export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [drawer, setDrawer] = useState(false);

  return (
    <div className="flex h-svh flex-col">
      <header className="flex items-center gap-2 border-b px-4 py-2">
        <Sheet open={drawer} onOpenChange={setDrawer}>
          <SheetTrigger asChild>
            <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open chats">
              <Menu />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-72 p-0">
            <SheetHeader className="sr-only">
              <SheetTitle>Chats</SheetTitle>
              <SheetDescription>Your chats, newest first</SheetDescription>
            </SheetHeader>
            <Suspense fallback={null}>
              <ChatList onNavigate={() => setDrawer(false)} />
            </Suspense>
          </SheetContent>
        </Sheet>
        <span className="font-medium">Personal Agent</span>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={() => {
            resetStore(); // the next owner must not see this chat's text
            logout();
            router.replace("/login");
          }}
        >
          Log out
        </Button>
      </header>
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-64 shrink-0 border-r md:flex md:flex-col">
          {/* The current chat comes from the address, which is runtime data */}
          <Suspense fallback={null}>
            <ChatList />
          </Suspense>
        </aside>
        <main className="flex min-w-0 flex-1 flex-col">{children}</main>
      </div>
    </div>
  );
}
