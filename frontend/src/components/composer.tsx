"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { SendResult } from "@/lib/chat-store";
import { COUNTER_FROM, MAX_MESSAGE_CHARS } from "@/lib/limits";
import { messageFor } from "@/lib/messages";

/**
 * Where a message is typed. Enter sends and Shift+Enter makes a new line. A message the server will
 * not take stays in the box with the reason; it is only cleared once the server has taken it.
 */
export function Composer({
  disabled = false,
  onSend,
}: {
  /** a reply is still arriving */
  disabled?: boolean;
  onSend: (text: string) => Promise<SendResult>;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);

  const length = text.length;
  const tooLong = length > MAX_MESSAGE_CHARS;
  const canSend = !disabled && !sending && text.trim() !== "" && !tooLong;

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canSend) return;
    setSending(true);
    setError(null);
    const result = await onSend(text.trim());
    setSending(false);
    if (result.ok) {
      setText("");
    } else {
      setError(result.error.code);
    }
    field.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter during IME composition confirms the characters; it is not a send
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center gap-2">
            <span>{messageFor(error)}</span>
            {error === "chat_full" && (
              <Button asChild size="sm" variant="outline">
                <Link href="/">New chat</Link>
              </Button>
            )}
          </AlertDescription>
        </Alert>
      )}
      <div className="flex items-end gap-2">
        <Textarea
          ref={field}
          aria-label="Message"
          placeholder="Message your assistant…"
          autoFocus
          rows={1}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          aria-invalid={tooLong ? true : undefined}
          className="max-h-48 min-h-10 resize-none field-sizing-content"
        />
        <Button type="submit" disabled={!canSend}>
          Send
        </Button>
      </div>
      {length >= COUNTER_FROM && (
        <p role="status" className={tooLong ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
          {length} / {MAX_MESSAGE_CHARS}
        </p>
      )}
    </form>
  );
}
