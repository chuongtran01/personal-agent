import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, vi } from "vitest";

import { getToken, setToken } from "@/auth/token";
import { failure, reply, fakeFetch } from "@/test/fetch";

import { createChat, getMessages, listChats, sendMessage } from "./chats";
import { setUnauthorizedHandler } from "./http";

const { fetchMock, lastCall } = fakeFetch();

const fixture = (name: string) =>
  readFileSync(path.join(process.cwd(), "src/test/fixtures", name));

test("creating a chat is a POST with no body", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(reply(201, { id: "c1" }));

  await createChat();

  expect(lastCall().url).toBe("/api/sessions");
  expect(lastCall().init.method).toBe("POST");
  expect(lastCall().init.body).toBeUndefined();
});

test("the chat list asks for a limit, 50 unless told otherwise", async () => {
  setToken("abc", 60);
  fetchMock.mockImplementation(async () => reply(200, [])); // a body can be read once

  await listChats();
  expect(lastCall().url).toBe("/api/sessions?limit=50");

  await listChats(10);
  expect(lastCall().url).toBe("/api/sessions?limit=10");
});

test("a chat id is encoded into the path", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(reply(200, []));

  await getMessages("a/b?c");

  expect(lastCall().url).toBe("/api/sessions/a%2Fb%3Fc/messages");
});

test("a chat's messages come back with the memories recalled for each reply", async () => {
  setToken("abc", 60);
  const messages = [
    { id: 1, role: "user", text: "hi", created_at: "2026-10-30T09:00:00Z", memories: [] },
    {
      id: 2,
      role: "assistant",
      text: "hello",
      created_at: "2026-10-30T09:00:02Z",
      memories: [{ id: 7, kind: "fact", content: "x", past: false, status: "active" }],
    },
  ];
  fetchMock.mockResolvedValue(reply(200, messages));

  expect(await getMessages("c1")).toEqual(messages);
});

// Sending --------------------------------------------------------------------------------------

test("a message that is taken streams its events", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(new Response(fixture("turn.sse"), { status: 200 }));
  const abort = new AbortController();

  const turn = await sendMessage("c1", "Where do I live?", abort.signal);
  const types: string[] = [];
  for await (const event of turn) types.push(event.type);

  expect(lastCall().url).toBe("/api/sessions/c1/messages");
  expect(lastCall().init.method).toBe("POST");
  expect(JSON.parse(String(lastCall().init.body))).toEqual({ text: "Where do I live?" });
  expect(lastCall().init.signal).toBe(abort.signal);
  expect(types[0]).toBe("memory");
  expect(types.at(-1)).toBe("done");
});

test.each([
  [409, "turn_in_progress"],
  [409, "chat_full"],
  [413, "message_too_long"],
  [422, "empty_message"],
  [404, "session_not_found"],
])("a message refused with %i %s throws before any stream starts", async (status, code) => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(reply(status, { detail: { code, message: "refused" } }));

  expect(await failure(sendMessage("c1", "hi"))).toMatchObject({ status, code });
});

test("an agent with no model key is told apart from login not being set up", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(reply(503, { detail: "the agent is not configured" }));

  expect(await failure(sendMessage("c1", "hi"))).toMatchObject({
    status: 503,
    code: "agent_not_configured",
  });
});

test("an answer with no body is an unreadable reply", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValue(new Response(null, { status: 200 }));

  expect(await failure(sendMessage("c1", "hi"))).toMatchObject({ code: "bad_stream" });
});

test("a refused token while sending signs the owner out", async () => {
  setToken("abc", 60);
  const handler = vi.fn();
  setUnauthorizedHandler(handler);
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  await failure(sendMessage("c1", "hi"));

  expect(getToken()).toBeNull();
  expect(handler).toHaveBeenCalledOnce();
});
