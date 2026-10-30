import { expect, test } from "vitest";

import { setToken } from "@/auth/token";
import { reply, fakeFetch } from "@/test/fetch";

import { listMemories } from "./memories";

const { fetchMock, lastCall } = fakeFetch();

test.each([
  [undefined, "/api/memories?limit=100&offset=0"],
  [{ status: "deleted" as const }, "/api/memories?limit=100&offset=0&status=deleted"],
  [
    { status: "active" as const, limit: 20, offset: 40 },
    "/api/memories?limit=20&offset=40&status=active",
  ],
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
