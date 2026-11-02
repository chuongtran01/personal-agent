"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/api/http";
import { deleteMemory } from "@/api/memories";
import type { Memory } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { messageFor } from "@/lib/messages";
import {
  EMPTY_TEXT,
  TABS,
  applyDeleted,
  filterMemories,
  formatDay,
  historyLine,
  holds,
  snippet,
  statusLabel,
  type MemoryTab,
} from "@/lib/memory-view";
import { relativeTime } from "@/lib/time";
import { useMemoryPages } from "@/lib/use-memories";
import { useMemoryHistory, type MemoryHistory } from "@/lib/use-memory-history";

// "Last shown", not "last used": recall puts a memory in the prompt and cannot tell which of them the
// model relied on (docs/PHASE4.md).
function Meta({ memory }: { memory: Memory }) {
  const when = holds(memory);
  const parts = [
    `Created ${formatDay(memory.created_at)}`,
    when && `Holds ${when}`,
    memory.last_used_at ? `Last shown ${relativeTime(memory.last_used_at)}` : "Never shown",
    memory.superseded_by !== null && `Replaced by memory ${memory.superseded_by}`,
  ].filter(Boolean);
  return <span>{parts.join(" · ")}</span>;
}

/** What happened to one memory, as the owner opened it. */
function HistoryList({ log }: { log: MemoryHistory }) {
  const { entries, loading, error, load } = log;

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex flex-col items-start gap-2">
          <span>Could not load the history. {messageFor(error)}</span>
          <Button size="sm" variant="outline" onClick={load}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
  if (entries === null || loading) {
    return (
      <div className="flex flex-col gap-2" aria-hidden>
        <Skeleton className="h-4" />
        <Skeleton className="h-4" />
      </div>
    );
  }
  return (
    <ol aria-label="History" className="flex flex-col gap-1 text-xs text-muted-foreground">
      {entries.map((entry) => (
        <li key={entry.id}>{historyLine(entry)}</li>
      ))}
    </ol>
  );
}

function Row({ memory, onDeleted }: { memory: Memory; onDeleted: (memory: Memory) => void }) {
  const [history, setHistory] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const deleteButton = useRef<HTMLButtonElement>(null);
  const log = useMemoryHistory(memory.id);
  const name = snippet(memory.content);

  function toggleHistory(open: boolean) {
    setHistory(open);
    if (open && log.entries === null) log.load();
  }

  function cancel() {
    setConfirming(false);
    setError(null);
    deleteButton.current?.focus();
  }

  async function confirmDelete() {
    setBusy(true);
    setError(null);
    try {
      await deleteMemory(memory.id);
      setBusy(false);
      setConfirming(false);
      setHistory(false);
      log.reset(); // it has a new line now, "Deleted"
      onDeleted({ ...memory, status: "deleted" });
    } catch (failure) {
      setBusy(false);
      setError(failure instanceof ApiError ? failure.code : "network");
    }
  }

  return (
    <li className="flex flex-col gap-1.5 border-b py-3 last:border-b-0">
      <div className="flex items-start gap-2">
        <Badge variant="outline" className="mt-0.5 shrink-0">
          {memory.kind}
        </Badge>
        <p
          className={cn("min-w-0 flex-1 break-words", memory.status === "deleted" && "text-muted-foreground")}
        >
          {memory.content}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        {memory.source_kind === "external" && <Badge variant="secondary">external</Badge>}
        {memory.status !== "active" && <Badge variant="secondary">{statusLabel(memory.status)}</Badge>}
        <Meta memory={memory} />
      </div>
      <Collapsible open={history} onOpenChange={toggleHistory} className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-1">
          <CollapsibleTrigger asChild>
            <Button size="sm" variant="ghost" aria-label={`History of: ${name}`}>
              History
            </Button>
          </CollapsibleTrigger>
          {memory.status !== "deleted" && (
            <Button
              ref={deleteButton}
              size="sm"
              variant="ghost"
              aria-label={`Delete: ${name}`}
              aria-expanded={confirming}
              onClick={() => setConfirming(true)}
            >
              Delete
            </Button>
          )}
        </div>
        <CollapsibleContent>
          <HistoryList log={log} />
        </CollapsibleContent>
      </Collapsible>
      {confirming && (
        <div
          role="group"
          aria-label={`Confirm deleting: ${name}`}
          onKeyDown={(event) => event.key === "Escape" && !busy && cancel()}
          className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/40 p-2 text-sm"
        >
          <span>This memory will not be used again. It stays listed under Deleted.</span>
          <Button size="sm" variant="destructive" onClick={() => void confirmDelete()} disabled={busy}>
            {busy ? "Deleting…" : "Delete memory"}
          </Button>
          <Button size="sm" variant="outline" onClick={cancel} disabled={busy} autoFocus>
            Cancel
          </Button>
        </div>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{messageFor(error)}</AlertDescription>
        </Alert>
      )}
    </li>
  );
}

function Panel({
  tab,
  active,
  filter,
  deleted,
  onDeleted,
}: {
  tab: MemoryTab;
  active: boolean;
  filter: string;
  /** deleted in this session, newest first, so every tab can show it without asking again */
  deleted: Memory[];
  onDeleted: (memory: Memory) => void;
}) {
  const status = TABS.find((t) => t.value === tab)!.status;
  const { rows: loaded, loading, error, done, started, loadMore } = useMemoryPages(status, active);
  const rows = applyDeleted(tab, loaded, deleted);
  const shown = filterMemories(rows, filter);
  const filtering = filter.trim() !== "";

  if (error && rows.length === 0) {
    return (
      <Alert variant="destructive">
        <AlertDescription className="flex flex-col items-start gap-2">
          <span>Could not load the memories. {messageFor(error)}</span>
          <Button size="sm" variant="outline" onClick={loadMore}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  }
  if (!started || (loading && rows.length === 0)) {
    return (
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
      </div>
    );
  }
  if (rows.length === 0) {
    return <p className="py-8 text-center text-muted-foreground">{EMPTY_TEXT[tab]}</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <p role="status" className="text-xs text-muted-foreground">
        {filtering ? `${shown.length} of ${rows.length} loaded match` : `${rows.length} loaded`}
        {!done && !filtering && " so far"}
      </p>
      {shown.length === 0 ? (
        <p className="py-6 text-center text-muted-foreground">
          No loaded memory matches &ldquo;{filter.trim()}&rdquo;.
          {!done && " Load more to search further."}
        </p>
      ) : (
        <ul>
          {shown.map((memory) => (
            <Row key={memory.id} memory={memory} onDeleted={onDeleted} />
          ))}
        </ul>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{messageFor(error)}</AlertDescription>
        </Alert>
      )}
      {!done && (
        <Button variant="outline" className="self-center" onClick={loadMore} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </Button>
      )}
    </div>
  );
}

/** What the assistant remembers, by status. A memory can be deleted here, and its history read. */
export function MemoryList() {
  const [tab, setTab] = useState<MemoryTab>("active");
  const [filter, setFilter] = useState("");
  const [deleted, setDeleted] = useState<Memory[]>([]);
  const notice = useRef<HTMLParagraphElement>(null);

  // The row that was deleted is gone, and with it the focus: say what happened and put focus there
  useEffect(() => {
    if (deleted.length > 0) notice.current?.focus();
  }, [deleted.length]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
        <div>
          <h1 className="text-xl font-medium">Memories</h1>
          <p className="text-sm text-muted-foreground">
            What the assistant remembers about you. To correct one, tell the assistant, for example
            &ldquo;Actually, I live in Porto now.&rdquo; You can delete a memory here.
          </p>
        </div>
        {deleted.length > 0 && (
          <p ref={notice} role="status" tabIndex={-1} className="text-sm outline-none">
            Memory deleted. It will not be used again, and is listed under Deleted.
          </p>
        )}
        <Tabs value={tab} onValueChange={(value) => setTab(value as MemoryTab)} className="gap-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <TabsList>
              {TABS.map((t) => (
                <TabsTrigger key={t.value} value={t.value}>
                  {t.label}
                </TabsTrigger>
              ))}
            </TabsList>
            <Input
              type="search"
              aria-label="Filter memories"
              placeholder="Filter loaded memories"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              className="sm:max-w-56"
            />
          </div>
          {/* Every tab stays mounted, so what a tab loaded is kept; only the shown one asks for more */}
          {TABS.map((t) => (
            <TabsContent key={t.value} value={t.value} forceMount className="data-[state=inactive]:hidden">
              <Panel
                tab={t.value}
                active={tab === t.value}
                filter={filter}
                deleted={deleted}
                onDeleted={(memory) => setDeleted((current) => [memory, ...current])}
              />
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </div>
  );
}
