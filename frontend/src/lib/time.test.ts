import { describe, expect, test } from "vitest";

import { relativeTime } from "./time";

const NOW = new Date("2026-10-30T12:00:00Z");
const ago = (seconds: number) => new Date(NOW.getTime() - seconds * 1000).toISOString();

describe("relativeTime", () => {
  test.each([
    [0, "just now"],
    [59, "just now"],
    [60, "1 minute ago"],
    [300, "5 minutes ago"],
    [3599, "59 minutes ago"],
    [3600, "1 hour ago"],
    [3 * 3600, "3 hours ago"],
    [86_400, "1 day ago"],
    [6 * 86_400, "6 days ago"],
  ])("%i seconds ago reads %s", (seconds, expected) => {
    expect(relativeTime(ago(seconds), NOW)).toBe(expected);
  });

  test("a week or more ago is the date, in the viewer's own time zone", () => {
    // Noon UTC falls on the 22nd to the 24th somewhere, so only the shape is checked
    expect(relativeTime(ago(7 * 86_400), NOW)).toMatch(/^Oct 2[234], 2026$/);
  });

  test("a time a little ahead of this clock is just now, not in the future", () => {
    expect(relativeTime(ago(-30), NOW)).toBe("just now");
  });

  test("something that is not a time is empty", () => {
    expect(relativeTime("not a date", NOW)).toBe("");
  });
});
