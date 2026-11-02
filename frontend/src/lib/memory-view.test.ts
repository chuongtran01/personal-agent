import { describe, expect, test } from "vitest";

import type { Memory, MemoryHistoryEntry } from "@/api/types";

import {
  EMPTY_TEXT,
  PAGE_SIZE,
  TABS,
  applyDeleted,
  filterMemories,
  formatDay,
  historyLine,
  holds,
  snippet,
  statusLabel,
} from "./memory-view";

const memory = (overrides: Partial<Memory> = {}): Memory => ({
  id: 1,
  kind: "fact",
  content: "The owner lives in Porto.",
  status: "active",
  source_kind: "owner",
  superseded_by: null,
  valid_from: null,
  valid_until: null,
  created_at: "2026-08-01T00:00:00Z",
  last_used_at: null,
  ...overrides,
});

describe("the tabs", () => {
  test("are active, replaced, deleted and all, in that order, with active first", () => {
    expect(TABS.map((t) => t.value)).toEqual(["active", "superseded", "deleted", "all"]);
    expect(TABS.map((t) => t.label)).toEqual(["Active", "Replaced", "Deleted", "All"]);
  });

  test("ask the server for their own status, and for every status on All", () => {
    expect(TABS.map((t) => t.status)).toEqual(["active", "superseded", "deleted", undefined]);
  });

  test("each has its own empty text", () => {
    expect(new Set(Object.values(EMPTY_TEXT)).size).toBe(TABS.length);
  });

  test("a page is the 100 the plan promises", () => {
    expect(PAGE_SIZE).toBe(100);
  });
});

describe("filterMemories", () => {
  const rows = [
    memory({ id: 1, content: "The owner lives in Porto." }),
    memory({ id: 2, kind: "preference", content: "Still water only." }),
    memory({ id: 3, content: "Email from the bank", source_kind: "external" }),
    memory({ id: 4, content: "Old address in Lisbon", status: "superseded" }),
  ];

  test("an empty or blank box keeps everything, in the same order", () => {
    expect(filterMemories(rows, "")).toBe(rows);
    expect(filterMemories(rows, "   ")).toBe(rows);
  });

  test("matches the text, in any case", () => {
    expect(filterMemories(rows, "PORTO").map((m) => m.id)).toEqual([1]);
  });

  test("matches the kind, the source and the status too", () => {
    expect(filterMemories(rows, "preference").map((m) => m.id)).toEqual([2]);
    expect(filterMemories(rows, "external").map((m) => m.id)).toEqual([3]);
    expect(filterMemories(rows, "superseded").map((m) => m.id)).toEqual([4]);
  });

  test("ignores spaces around what was typed, and finds nothing when nothing matches", () => {
    expect(filterMemories(rows, "  lisbon ").map((m) => m.id)).toEqual([4]);
    expect(filterMemories(rows, "zebra")).toEqual([]);
  });

  test("does not change the list it was given", () => {
    const before = structuredClone(rows);

    filterMemories(rows, "porto");

    expect(rows).toEqual(before);
  });
});

describe("holds", () => {
  const A = "2027-01-04T12:00:00Z";
  const B = "2026-03-03T12:00:00Z";

  test("says nothing for a memory that always holds", () => {
    expect(holds({ valid_from: null, valid_until: null })).toBe("");
  });

  test("a start, an end, or both", () => {
    expect(holds({ valid_from: A, valid_until: null })).toBe("from Jan 4, 2027");
    expect(holds({ valid_from: null, valid_until: B })).toBe("until Mar 3, 2026");
    expect(holds({ valid_from: B, valid_until: A })).toBe("Mar 3, 2026 to Jan 4, 2027");
  });
});

describe("formatDay", () => {
  test("shows a day as the server stores it, whatever time zone the browser is in", () => {
    // A date kept as midnight UTC is the day itself, not the evening before it in the Americas
    expect(formatDay("2027-01-04T00:00:00Z")).toBe("Jan 4, 2027");
    expect(formatDay("2027-01-04T23:59:00Z")).toBe("Jan 4, 2027");
  });
});

test("formatDay is empty for something that is not a date", () => {
  expect(formatDay("nonsense")).toBe("");
});

test("a superseded memory is called replaced, and the others keep their word", () => {
  expect(statusLabel("superseded")).toBe("replaced");
  expect(statusLabel("active")).toBe("active");
  expect(statusLabel("deleted")).toBe("deleted");
});

// Deleting and the history ------------------------------------------------------------------------

describe("applyDeleted", () => {
  const row = (id: number, status: Memory["status"] = "active"): Memory => ({
    id,
    kind: "fact",
    content: `Memory ${id}.`,
    status,
    source_kind: "owner",
    superseded_by: null,
    valid_from: null,
    valid_until: null,
    created_at: "2026-08-01T00:00:00Z",
    last_used_at: null,
  });
  const gone = (id: number) => row(id, "deleted");

  test("with nothing deleted hands back the very same rows", () => {
    const rows = [row(1), row(2)];

    for (const tab of ["active", "superseded", "deleted", "all"] as const) {
      expect(applyDeleted(tab, rows, [])).toBe(rows);
    }
  });

  test.each(["active", "superseded"] as const)("the %s tab drops what was deleted", (tab) => {
    expect(applyDeleted(tab, [row(1), row(2), row(3)], [gone(2)]).map((m) => m.id)).toEqual([1, 3]);
  });

  test("the All tab keeps it, as deleted", () => {
    const shown = applyDeleted("all", [row(1), row(2)], [gone(2)]);

    expect(shown.map((m) => [m.id, m.status])).toEqual([
      [1, "active"],
      [2, "deleted"],
    ]);
  });

  test("the Deleted tab puts the new ones first, newest first, before what the server sent", () => {
    const shown = applyDeleted("deleted", [gone(9), gone(8)], [gone(3), gone(2)]);

    expect(shown.map((m) => m.id)).toEqual([3, 2, 9, 8]);
  });

  test("the Deleted tab does not show one twice once the server has it too", () => {
    const shown = applyDeleted("deleted", [gone(3), gone(8)], [gone(3), gone(2)]);

    expect(shown.map((m) => m.id)).toEqual([2, 3, 8]);
  });

  test("does not change the rows it was given", () => {
    const rows = [row(1), row(2)];

    applyDeleted("all", rows, [gone(2)]);
    applyDeleted("active", rows, [gone(2)]);

    expect(rows.map((m) => m.status)).toEqual(["active", "active"]);
    expect(rows).toHaveLength(2);
  });
});

describe("historyLine", () => {
  const entry = (overrides: Partial<MemoryHistoryEntry> = {}): MemoryHistoryEntry => ({
    id: 1,
    event: "created",
    reason: "the owner said it",
    message_id: null,
    created_at: "2026-08-01T12:00:00Z",
    ...overrides,
  });

  test("says what happened, when, and why, with the reason capitalised", () => {
    expect(historyLine(entry())).toBe("Written Aug 1, 2026 · The owner said it");
  });

  test.each([
    ["superseded", "Replaced"],
    ["deleted", "Deleted"],
    ["restored", "Restored"],
  ])("calls a %s event %s", (event, word) => {
    expect(historyLine(entry({ event }))).toMatch(new RegExp(`^${word} Aug 1, 2026`));
  });

  test("says when a change came from a chat message, and not when it was the owner's own", () => {
    expect(historyLine(entry({ message_id: 12 }))).toMatch(/· from a chat message$/);
    expect(historyLine(entry({ message_id: null }))).not.toMatch(/chat message/);
  });

  test("keeps an event it has no word for, and copes with a missing reason or date", () => {
    expect(historyLine(entry({ event: "archived", reason: "" }))).toBe("archived Aug 1, 2026");
    expect(historyLine(entry({ created_at: "nonsense" }))).toBe("Written · The owner said it");
  });
});

describe("snippet", () => {
  test("keeps short text, flattens white space and cuts long text with an ellipsis", () => {
    expect(snippet("  Lives   in\nPorto. ")).toBe("Lives in Porto.");
    expect(snippet("x".repeat(60))).toBe(`${"x".repeat(39)}…`);
    expect(snippet("x".repeat(40))).toBe("x".repeat(40));
  });
});
