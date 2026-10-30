"use client";

import { useState } from "react";

import type { Memory } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { messageFor } from "@/lib/messages";
import {
  EMPTY_TEXT,
  TABS,
  filterMemories,
  formatDay,
  holds,
  statusLabel,
  type MemoryTab,
} from "@/lib/memory-view";
import { relativeTime } from "@/lib/time";
import { useMemoryPages } from "@/lib/use-memories";

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

function Row({ memory }: { memory: Memory }) {
  return (
    <li className="flex flex-col gap-1.5 border-b py-3 last:border-b-0">
      <div className="flex items-start gap-2">
        <Badge variant="outline" className="mt-0.5 shrink-0">
          {memory.kind}
        </Badge>
        <p className={cn("min-w-0 flex-1 break-words", memory.status === "deleted" && "text-muted-foreground")}>
          {memory.content}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        {memory.source_kind === "external" && <Badge variant="secondary">external</Badge>}
        {memory.status !== "active" && <Badge variant="secondary">{statusLabel(memory.status)}</Badge>}
        <Meta memory={memory} />
      </div>
    </li>
  );
}

function Panel({ tab, active, filter }: { tab: MemoryTab; active: boolean; filter: string }) {
  const status = TABS.find((t) => t.value === tab)!.status;
  const { rows, loading, error, done, started, loadMore } = useMemoryPages(status, active);
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
            <Row key={memory.id} memory={memory} />
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

/** What the assistant remembers, by status. Read-only: editing and deleting arrive with phase 5. */
export function MemoryList() {
  const [tab, setTab] = useState<MemoryTab>("active");
  const [filter, setFilter] = useState("");

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
        <div>
          <h1 className="text-xl font-medium">Memories</h1>
          <p className="text-sm text-muted-foreground">
            What the assistant remembers about you. For now this is a view only.
          </p>
        </div>
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
              <Panel tab={t.value} active={tab === t.value} filter={filter} />
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </div>
  );
}
