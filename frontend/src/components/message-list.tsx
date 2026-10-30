import type { ChatMessage } from "@/api/types";
import { Markdown } from "@/components/markdown";
import { RecalledMemories } from "@/components/recalled-memories";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { ActiveTurn } from "@/lib/chat-state";

function Question({ text }: { text: string }) {
  return (
    <div className="ml-auto max-w-[85%] rounded-2xl bg-muted px-4 py-2 text-foreground whitespace-pre-wrap break-words">
      {text}
    </div>
  );
}

function Reply({ message }: { message: ChatMessage }) {
  return (
    <div className="flex flex-col gap-2">
      <Markdown>{message.text}</Markdown>
      <RecalledMemories memories={message.memories} />
    </div>
  );
}

function Pending({ turn, onReload }: { turn: ActiveTurn; onReload: () => void }) {
  if (turn.status === "failed" && turn.error) {
    const lost = turn.error.code === "connection_lost";
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex flex-col items-start gap-2">
          <span>{turn.error.message}</span>
          {lost ? (
            <Button size="sm" variant="outline" onClick={onReload}>
              Reload
            </Button>
          ) : (
            <span>Your message was saved but not answered. Send it again to retry.</span>
          )}
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <div className="flex flex-col gap-2" aria-busy="true">
      {turn.text ? <Markdown>{turn.text}</Markdown> : <p className="text-muted-foreground">Thinking…</p>}
      <RecalledMemories memories={turn.memories} />
    </div>
  );
}

/** The conversation. A log region, so a screen reader hears the reply as it arrives. */
export function MessageList({
  messages,
  turn,
  onReload,
}: {
  messages: ChatMessage[];
  turn: ActiveTurn | null;
  onReload: () => void;
}) {
  return (
    <div role="log" aria-live="polite" aria-label="Conversation" className="flex flex-col gap-6">
      {messages.map((message) =>
        message.role === "user" ? (
          <Question key={message.id} text={message.text} />
        ) : (
          <Reply key={message.id} message={message} />
        ),
      )}
      {turn && <Pending turn={turn} onReload={onReload} />}
    </div>
  );
}
