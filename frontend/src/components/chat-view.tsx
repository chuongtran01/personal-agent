"use client";

import { useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useRef } from "react";

import { Composer } from "@/components/composer";
import { MessageList } from "@/components/message-list";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { isBusy } from "@/lib/chat-state";
import { loadChat, send } from "@/lib/chat-store";
import { messageFor } from "@/lib/messages";
import { useChat } from "@/lib/use-chat";

const NO_SUCH_CHAT = new Set(["session_not_found", "invalid_request", "not_found"]);
const FOLLOW_WITHIN_PX = 80;

export function ChatSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 p-4" aria-hidden>
      <Skeleton className="ml-auto h-10 w-2/3" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="ml-auto h-10 w-1/2" />
    </div>
  );
}

export function ChatView({ chatId }: { chatId: string }) {
  const router = useRouter();
  const chat = useChat(chatId);
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  useEffect(() => {
    void loadChat(chatId);
  }, [chatId]);

  // A chat that does not exist is not a page to stay on
  useEffect(() => {
    if (chat.loadError && NO_SUCH_CHAT.has(chat.loadError)) router.replace("/");
  }, [chat.loadError, router]);

  // The view follows the reply only while the owner is already at the bottom
  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && following.current) element.scrollTop = element.scrollHeight;
  }, [chat.messages, chat.turn, chat.loaded]);

  function onScroll() {
    const element = scroller.current;
    if (!element) return;
    following.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < FOLLOW_WITHIN_PX;
  }

  async function onSend(text: string) {
    following.current = true; // sending is a return to the bottom
    const result = await send(chatId, text);
    if (!result.ok && NO_SUCH_CHAT.has(result.error.code)) router.replace("/");
    return result;
  }

  const failedToLoad = chat.loadError && !NO_SUCH_CHAT.has(chat.loadError);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scroller} onScroll={onScroll} className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl p-4">
          {!chat.loaded ? (
            <ChatSkeleton />
          ) : failedToLoad ? (
            <Alert variant="destructive">
              <AlertDescription className="flex flex-col items-start gap-2">
                <span>Could not load this chat. {messageFor(chat.loadError!)}</span>
                <Button size="sm" variant="outline" onClick={() => void loadChat(chatId)}>
                  Try again
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            <MessageList
              messages={chat.messages}
              turn={chat.turn}
              onReload={() => void loadChat(chatId)}
            />
          )}
        </div>
      </div>
      <div className="border-t">
        <div className="mx-auto w-full max-w-2xl p-4">
          <Composer disabled={isBusy(chat) || !chat.loaded} onSend={onSend} />
        </div>
      </div>
    </div>
  );
}
