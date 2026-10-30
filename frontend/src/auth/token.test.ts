import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { clearToken, getToken, setToken } from "./token";

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

test("a token comes back until it expires", () => {
  setToken("abc", 60, 1_000);

  expect(getToken(1_000)).toBe("abc");
  expect(getToken(60_999)).toBe("abc");
});

test("an expired token is gone, and is removed from storage", () => {
  setToken("abc", 60, 1_000);

  expect(getToken(61_000)).toBeNull();
  expect(sessionStorage.length).toBe(0);
});

test("no token is null", () => {
  expect(getToken()).toBeNull();
});

test("clearing removes it", () => {
  setToken("abc", 60);

  clearToken();

  expect(getToken()).toBeNull();
});

test("it lives in sessionStorage, which a closed tab loses, and not in localStorage", () => {
  setToken("abc", 60);

  expect(sessionStorage.length).toBe(1);
  expect(localStorage.length).toBe(0);
});

test.each([
  ["text that is not JSON", "{oops"],
  ["JSON of the wrong shape", JSON.stringify({ token: 5 })],
  ["a missing expiry", JSON.stringify({ token: "abc" })],
])("%s in storage is treated as signed out and cleared", (_, stored) => {
  sessionStorage.setItem("personal-agent.token", stored);

  expect(getToken()).toBeNull();
  expect(sessionStorage.length).toBe(0);
});

test("storage that throws means signed out, not a crash", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });

  expect(() => setToken("abc", 60)).not.toThrow();
  expect(getToken()).toBeNull();
  expect(() => clearToken()).not.toThrow();
});
