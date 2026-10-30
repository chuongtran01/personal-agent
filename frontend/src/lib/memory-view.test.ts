import { describe, expect, test } from "vitest";

import type { Memory } from "@/api/types";

import { EMPTY_TEXT, PAGE_SIZE, TABS, filterMemories, formatDay, holds, statusLabel } from "./memory-view";

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
