import { describe, expect, test } from "vitest";

import { loginUrl, safeNext } from "./redirect";

describe("safeNext", () => {
  test.each([
    ["/memories", "/memories"],
    ["/chats/abc", "/chats/abc"],
    ["/chats/abc?tab=1", "/chats/abc?tab=1"],
    ["/", "/"],
  ])("follows a path on this site: %s", (next, expected) => {
    expect(safeNext(next)).toBe(expected);
  });

  test.each([
    ["nothing", null],
    ["nothing at all", undefined],
    ["an empty value", ""],
    ["a relative path", "memories"],
    ["another site", "https://evil.example/steal"],
    ["a protocol-relative URL", "//evil.example"],
    ["a protocol-relative URL with a backslash", "/\\evil.example"],
    ["a backslash anywhere", "/ok\\path"],
    ["a javascript: URL", "javascript:alert(1)"],
    ["a data: URL", "data:text/html,<script>alert(1)</script>"],
    ["a tab that the URL parser would drop", "/\t/evil.example"],
    ["a newline that the URL parser would drop", "/\n/evil.example"],
    ["the login page", "/login"],
    ["the login page with a query", "/login?next=/memories"],
    ["a page under login", "/login/help"],
  ])("goes home for %s", (_, next) => {
    expect(safeNext(next)).toBe("/");
  });

  test("a path that only starts like the login page is fine", () => {
    expect(safeNext("/loginfo")).toBe("/loginfo");
  });
});

describe("loginUrl", () => {
  test.each([
    ["/", "/login"],
    ["/login", "/login"],
    ["/memories", "/login?next=%2Fmemories"],
    ["/chats/a?b=1", "/login?next=%2Fchats%2Fa%3Fb%3D1"],
  ])("from %s is %s", (from, expected) => {
    expect(loginUrl(from)).toBe(expected);
  });

  test("never passes on an address it would not follow", () => {
    expect(loginUrl("//evil.example")).toBe("/login");
    expect(loginUrl("javascript:alert(1)")).toBe("/login");
  });

  test("what it writes, safeNext reads back", () => {
    const url = loginUrl("/chats/a?b=1&c=2");
    const next = new URLSearchParams(url.split("?")[1]).get("next");

    expect(safeNext(next)).toBe("/chats/a?b=1&c=2");
  });
});
