import { expect, test, vi } from "vitest";

import { getToken, setToken } from "@/auth/token";
import { failure, reply, fakeFetch } from "@/test/fetch";

import { renaming, request, setUnauthorizedHandler } from "./http";

const { fetchMock, lastCall, headers } = fakeFetch();

// Token and prefix ---------------------------------------------------------------------------

test("a call goes to /api and carries the token", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(reply(200, []));

  await request("/sessions");

  expect(lastCall().url).toBe("/api/sessions");
  expect(headers().authorization).toBe("Bearer abc");
});

test("with no token the call carries none", async () => {
  fetchMock.mockResolvedValue(reply(200, []));

  await request("/sessions");

  expect(headers().authorization).toBeUndefined();
});

test("a call that opts out of auth carries no token even when there is one", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(reply(200, {}));

  await request("/auth/login", { auth: false });

  expect(headers().authorization).toBeUndefined();
});

test("a body is sent as JSON, and no body means no content type", async () => {
  fetchMock.mockResolvedValue(reply(200, {}));

  await request("/x", { method: "POST", body: { a: 1 } });
  expect(headers()["content-type"]).toBe("application/json");
  expect(lastCall().init.body).toBe('{"a":1}');

  await request("/x", { method: "POST" });
  expect(headers()["content-type"]).toBeUndefined();
  expect(lastCall().init.body).toBeUndefined();
});

// Errors as codes ----------------------------------------------------------------------------

test("the backend's error body becomes its code and message", async () => {
  fetchMock.mockResolvedValue(
    reply(404, { detail: { code: "session_not_found", message: "no such chat" } }),
  );

  const error = await failure(request("/x"));

  expect(error).toMatchObject({ status: 404, code: "session_not_found", message: "no such chat" });
});

test.each([
  [404, { detail: "Not Found" }, "not_found"],
  [422, { detail: [{ loc: ["body"], msg: "bad" }] }, "invalid_request"],
  [503, { detail: "the agent is not configured" }, "unavailable"],
  [500, "<html>oops</html>", "server_error"],
  [418, {}, "http_error"],
])("a %i without a code of its own is %s", async (status, body, code) => {
  fetchMock.mockResolvedValue(reply(status, body));

  expect(await failure(request("/x"))).toMatchObject({ status, code });
});

test("an unreachable server is a network error with status 0", async () => {
  fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

  expect(await failure(request("/x"))).toMatchObject({ status: 0, code: "network" });
});

test("an aborted request throws its AbortError and not an ApiError", async () => {
  fetchMock.mockRejectedValue(new DOMException("aborted", "AbortError"));

  await expect(request("/x")).rejects.toMatchObject({ name: "AbortError" });
});

// A refused or missing token ------------------------------------------------------------------

test("a 401 clears the token and tells the app, once", async () => {
  setToken("abc", 60);
  const handler = vi.fn();
  setUnauthorizedHandler(handler);
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  const error = await failure(request("/sessions"));

  expect(error).toMatchObject({ status: 401, code: "unauthorized" });
  expect(getToken()).toBeNull();
  expect(handler).toHaveBeenCalledOnce();
});

test("a 401 on a call that opted out of auth leaves the token and the app alone", async () => {
  setToken("abc", 60);
  const handler = vi.fn();
  setUnauthorizedHandler(handler);
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  await failure(request("/auth/login", { auth: false }));

  expect(getToken()).toBe("abc");
  expect(handler).not.toHaveBeenCalled();
});

// Renaming -------------------------------------------------------------------------------------

test("renaming changes the code of one status and keeps the message", async () => {
  fetchMock.mockResolvedValue(reply(503, { detail: "the agent is not configured" }));

  const error = await failure(renaming(503, "agent_not_configured", () => request("/x")));

  expect(error).toMatchObject({
    status: 503,
    code: "agent_not_configured",
    message: "the agent is not configured",
  });
});

test("renaming leaves other statuses, and other kinds of failure, as they were", async () => {
  fetchMock.mockResolvedValue(reply(409, { detail: { code: "chat_full", message: "full" } }));
  expect(await failure(renaming(503, "x", () => request("/x")))).toMatchObject({
    code: "chat_full",
  });

  const boom = new Error("not an api error");
  await expect(renaming(503, "x", () => Promise.reject(boom))).rejects.toBe(boom);
});
