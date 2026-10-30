import type { Memory, MemoryStatus } from "@/api/types";

/** The memories the server sends per page, and so when "load more" has nothing more to ask for. */
export const PAGE_SIZE = 100;

export type MemoryTab = "active" | "superseded" | "deleted" | "all";

export const TABS: { value: MemoryTab; label: string; status: MemoryStatus | undefined }[] = [
  { value: "active", label: "Active", status: "active" },
  { value: "superseded", label: "Replaced", status: "superseded" },
  { value: "deleted", label: "Deleted", status: "deleted" },
  { value: "all", label: "All", status: undefined },
];

export const EMPTY_TEXT: Record<MemoryTab, string> = {
  active: "No active memories yet.",
  superseded: "Nothing has been replaced yet.",
  deleted: "Nothing has been deleted.",
  all: "No memories yet.",
};

/** The loaded memories that match what was typed: its text, its kind, or its status, in any case. */
export function filterMemories(memories: Memory[], query: string): Memory[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return memories;
  return memories.filter((m) =>
    [m.content, m.kind, m.status, m.source_kind].some((field) => field.toLowerCase().includes(needle)),
  );
}

// In UTC, which is how the server stores them: a date such as "4 January 2027" is kept as that day's
// midnight UTC, and shown in a time zone west of Greenwich it would read as the 3rd.
const DAY = new Intl.DateTimeFormat("en", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

export function formatDay(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : DAY.format(date);
}

/** When the memory holds, in words: "from 4 Jan 2027", "until 3 Mar 2026", or both. Empty when it always does. */
export function holds(memory: Pick<Memory, "valid_from" | "valid_until">): string {
  const from = memory.valid_from ? formatDay(memory.valid_from) : "";
  const until = memory.valid_until ? formatDay(memory.valid_until) : "";
  if (from && until) return `${from} to ${until}`;
  if (from) return `from ${from}`;
  if (until) return `until ${until}`;
  return "";
}

/** The word for a memory's status: "replaced" is what a superseded memory is called in the app. */
export function statusLabel(status: MemoryStatus): string {
  return status === "superseded" ? "replaced" : status;
}
