import { expect, test, vi } from "vitest";

import { setToken } from "@/auth/token";
import { failure, reply, fakeFetch } from "@/test/fetch";

import { setUnauthorizedHandler } from "./http";
import { deleteMemory, getMemoryHistory, listMemories } from "./memories";

const { fetchMock, lastCall } = fakeFetch();

test.each([
  [undefined, "/api/memories?limit=100&offset=0"],
  [{ status: "deleted" as const }, "/api/memories?limit=100&offset=0&status=deleted"],
  [{ status: "active" as const, limit: 20, offset: 40 }, "/api/memories?limit=20&offset=40&status=active"],
])("the memory list asks for %j", async (query, url) => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(reply(200, []));

  await listMemories(query);

  expect(lastCall().url).toBe(url);
});

test("the memories come back as the server sent them", async () => {
  setToken("abc", 60);
  const rows = [
    {
      id: 3,
      kind: "fact",
      content: "The owner lives in Porto.",
      status: "active",
      source_kind: "owner",
      superseded_by: null,
      valid_from: null,
      valid_until: null,
      created_at: "2026-08-01T00:00:00Z",
      last_used_at: null,
    },
  ];
  fetchMock.mockResolvedValue(reply(200, rows));

  expect(await listMemories()).toEqual(rows);
});

// Deleting and the history ------------------------------------------------------------------------

test("a delete is a DELETE to that memory, with the token, and resolves to nothing", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

  await expect(deleteMemory(7)).resolves.toBeUndefined();

  expect(lastCall().url).toBe("/api/memories/7");
  expect(lastCall().init.method).toBe("DELETE");
  expect((lastCall().init.headers as Record<string, string>).authorization).toBe("Bearer abc");
  expect(lastCall().init.body).toBeUndefined();
});

test("a delete of a memory that is gone fails with the server's code", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(
    reply(404, {
      detail: { code: "memory_not_found", message: "no memory 7" },
    }),
  );

  const error = await failure(deleteMemory(7));

  expect([error.status, error.code]).toEqual([404, "memory_not_found"]);
});

test("a refused token on a delete signs the owner out like any other call", async () => {
  setToken("abc", 60);
  const onUnauthorized = vi.fn();
  setUnauthorizedHandler(onUnauthorized);
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  const error = await failure(deleteMemory(7));

  expect(error.code).toBe("unauthorized");
  expect(onUnauthorized).toHaveBeenCalledOnce();
});

test("the history of a memory is read from its events and comes back as sent", async () => {
  setToken("abc", 60);
  const entries = [
    {
      id: 1,
      event: "created",
      reason: "the owner said it",
      message_id: 4,
      created_at: "2026-08-01T00:00:00Z",
    },
    {
      id: 2,
      event: "deleted",
      reason: "deleted by the owner in the app",
      message_id: null,
      created_at: "2026-08-02T00:00:00Z",
    },
  ];
  fetchMock.mockResolvedValue(reply(200, entries));

  const history = await getMemoryHistory(7);

  expect(lastCall().url).toBe("/api/memories/7/events");
  expect(lastCall().init.method ?? "GET").toBe("GET");
  expect(history).toEqual(entries);
});

test("the history of an unknown memory fails with a code", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(
    reply(404, {
      detail: { code: "memory_not_found", message: "no memory 9" },
    }),
  );

  expect((await failure(getMemoryHistory(9))).code).toBe("memory_not_found");
});
