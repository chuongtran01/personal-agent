"use client";

import { useRouter } from "next/navigation";

import { Composer } from "@/components/composer";
import { startChat, type SendResult } from "@/lib/chat-store";

/** A chat is not created until its first message is sent, so empty chats do not pile up. */
export function NewChat() {
  const router = useRouter();

  async function onSend(text: string): Promise<SendResult> {
    const started = await startChat(text);
    if (!started.ok) return started;
    router.push(`/chats/${encodeURIComponent(started.id)}`); // the reply keeps streaming in the store
    return { ok: true };
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 p-4">
      <h1 className="text-center text-2xl font-medium">What&apos;s on your mind?</h1>
      <Composer onSend={onSend} />
    </div>
  );
}
