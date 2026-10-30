"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { refreshChatList } from "@/lib/chat-store";
import { relativeTime } from "@/lib/time";
import { useChatList } from "@/lib/use-chat";

export function ChatList({ onNavigate }: { onNavigate?: () => void }) {
  const { items, error } = useChatList();
  const pathname = usePathname();

  useEffect(() => {
    void refreshChatList();
  }, []);

  return (
    <nav aria-label="Chats" className="flex min-h-0 flex-1 flex-col gap-2 p-2">
      <Button asChild variant="outline">
        <Link href="/" onClick={onNavigate}>
          New chat
        </Link>
      </Button>
      <ScrollArea className="min-h-0 flex-1">
        {items === null && !error && (
          <div className="flex flex-col gap-2 p-1" aria-hidden>
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </div>
        )}
        {error && (
          <div className="flex flex-col items-start gap-2 p-2 text-sm">
            <span>Could not load your chats.</span>
            <Button size="sm" variant="outline" onClick={() => void refreshChatList()}>
              Try again
            </Button>
          </div>
        )}
        {items?.length === 0 && <p className="p-2 text-sm text-muted-foreground">No chats yet.</p>}
        <ul className="flex flex-col gap-1">
          {items?.map((chat) => {
            const href = `/chats/${encodeURIComponent(chat.id)}`;
            const current = pathname === href;
            return (
              <li key={chat.id}>
                <Link
                  href={href}
                  onClick={onNavigate}
                  aria-current={current ? "page" : undefined}
                  className="flex flex-col rounded-lg px-3 py-2 text-sm hover:bg-muted aria-[current=page]:bg-muted"
                >
                  <span className="truncate">{chat.preview ?? "New chat"}</span>
                  <span className="text-xs text-muted-foreground">
                    {relativeTime(chat.created_at)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </ScrollArea>
    </nav>
  );
}
