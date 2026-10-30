"use client";

import { Brain, CalendarDays, SlidersHorizontal, type LucideIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Composer } from "@/components/composer";
import { startChat, type SendResult } from "@/lib/chat-store";

// Things worth asking a memory. Each only fills the box, so the owner decides what is sent
const SUGGESTIONS: { icon: LucideIcon; text: string }[] = [
  { icon: Brain, text: "What do you remember about me?" },
  { icon: CalendarDays, text: "What do I have coming up?" },
  { icon: SlidersHorizontal, text: "What are my preferences?" },
];

/** A chat is not created until its first message is sent, so empty chats do not pile up. */
export function NewChat() {
  const router = useRouter();
  // Choosing a suggestion mounts the composer again with its text in the box
  const [suggestion, setSuggestion] = useState({ text: "", chosen: 0 });

  async function onSend(text: string): Promise<SendResult> {
    const started = await startChat(text);
    if (!started.ok) return started;
    router.push(`/chats/${encodeURIComponent(started.id)}`); // the reply keeps streaming in the store
    return { ok: true };
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-6 px-4 pb-24">
      <h1 className="text-center text-2xl font-medium">What&apos;s on your mind?</h1>
      <Composer key={suggestion.chosen} initialText={suggestion.text} onSend={onSend} />
      <ul className="flex flex-col text-sm text-muted-foreground" aria-label="Suggestions">
        {SUGGESTIONS.map(({ icon: Icon, text }) => (
          <li key={text}>
            <button
              type="button"
              onClick={() => setSuggestion((s) => ({ text, chosen: s.chosen + 1 }))}
              className="flex w-full items-center gap-3 rounded-lg px-4 py-2 text-left hover:bg-muted hover:text-foreground"
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {text}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
