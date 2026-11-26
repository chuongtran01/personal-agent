import { describe, expect, test } from "vitest";

import { toolPhrases } from "./tool-labels";

describe("what the chat says about the tools a reply used", () => {
  test("says nothing when no tool was used or it was not recorded", () => {
    expect(toolPhrases([])).toEqual([]);
    expect(toolPhrases(null)).toEqual([]);
    expect(toolPhrases(undefined)).toEqual([]);
  });

  test.each([
    ["email_search", "Searched your email"],
    ["email_read", "Read an email"],
    ["calendar_list_events", "Looked at your calendar"],
    ["search_memory", "Searched memory"],
  ])("has words for %s", (name, phrase) => {
    expect(toolPhrases([name])).toEqual([phrase]);
  });

  test("counts a tool used more than once", () => {
    expect(toolPhrases(["email_read", "email_read", "email_read"])).toEqual(["Read 3 emails"]);
    expect(toolPhrases(["email_search", "email_search"])).toEqual(["Searched your email 2 times"]);
    expect(toolPhrases(["calendar_list_events", "calendar_list_events"])).toEqual([
      "Looked at your calendar 2 times",
    ]);
    expect(toolPhrases(["search_memory", "search_memory"])).toEqual(["Searched memory 2 times"]);
  });

  test("gives one phrase per kind of tool, in the order each was first used", () => {
    expect(toolPhrases(["email_search", "email_read", "search_memory", "email_read"])).toEqual([
      "Searched your email",
      "Read 2 emails",
      "Searched memory",
    ]);
  });

  test("says a tool it has no words for was used, and never shows its name", () => {
    expect(toolPhrases(["calendar_create_event"])).toEqual(["Used a tool"]);
    expect(toolPhrases(["calendar_create_event", "email_send"])).toEqual(["Used tools 2 times"]);
  });

  test("is not fooled by a name that is also a property of every object", () => {
    expect(toolPhrases(["constructor", "__proto__", "toString"])).toEqual(["Used tools 3 times"]);
  });
});
