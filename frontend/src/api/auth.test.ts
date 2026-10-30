import { expect, test, vi } from "vitest";

import { getToken, setToken } from "@/auth/token";
import { failure, reply, fakeFetch } from "@/test/fetch";

import { login, logout } from "./auth";
import { setUnauthorizedHandler } from "./http";

const { fetchMock, lastCall, headers } = fakeFetch();

test("a login keeps the token for its lifetime", async () => {
  fetchMock.mockResolvedValue(
    reply(200, { access_token: "tok", token_type: "bearer", expires_in: 3600 }),
  );

  await login("correct horse");

  expect(lastCall().url).toBe("/api/auth/login");
  expect(JSON.parse(String(lastCall().init.body))).toEqual({ password: "correct horse" });
  expect(headers()["content-type"]).toBe("application/json");
  expect(headers().authorization).toBeUndefined();
  expect(getToken()).toBe("tok");
  expect(getToken(Date.now() + 3_599_000)).toBe("tok");
  expect(getToken(Date.now() + 3_601_000)).toBeNull();
});

test("a wrong password is its own code and does not sign the owner out", async () => {
  setToken("still-valid", 60);
  const handler = vi.fn();
  setUnauthorizedHandler(handler);
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  const error = await failure(login("nope"));

  expect(error).toMatchObject({ status: 401, code: "wrong_password" });
  expect(handler).not.toHaveBeenCalled();
  expect(getToken()).toBe("still-valid");
});

test("a server with login not set up is told apart from a wrong password", async () => {
  fetchMock.mockResolvedValue(reply(503, { detail: "owner login is not configured" }));

  expect(await failure(login("x"))).toMatchObject({ status: 503, code: "login_not_configured" });
});

test("a failed login stores nothing", async () => {
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  await failure(login("nope"));

  expect(getToken()).toBeNull();
});

test("logging out clears the token", () => {
  setToken("abc", 60);

  logout();

  expect(getToken()).toBeNull();
});
