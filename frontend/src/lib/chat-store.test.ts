import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { ChatMessage, ChatSummary } from "@/api/types";
import { setToken } from "@/auth/token";
import { fakeFetch, reply, routeFetch, callsTo, type Handler } from "@/test/fetch";
import { DONE, MEMORY, liveReply } from "@/test/stream";

import { isBusy } from "./chat-state";
import {
  getChat,
  getChatList,
  loadChat,
  refreshChatList,
  resetStore,
  send,
  startChat,
  subscribe,
} from "./chat-store";

const { fetchMock } = fakeFetch();

const summary = (id: string, preview: string | null = null, count = 0): ChatSummary => ({
  id,
  created_at: "2026-10-30T09:00:00Z",
  message_count: count,
  preview,
});
const stored = (id: number, role: "user" | "assistant", text: string): ChatMessage => ({
  id,
  role,
  text,
  created_at: "2026-10-30T09:00:00Z",
  memories: [],
});

const routes = (table: Record<string, Handler>) => routeFetch(fetchMock, table);
const calls = (key: string) => callsTo(fetchMock, key);

beforeEach(() => {
  resetStore();
  setToken("abc", 600);
});
afterEach(() => {
  vi.useRealTimers();
});

// The history ----------------------------------------------------------------------------------

describe("loading a chat", () => {
  test("keeps the history the server has", async () => {
    routes({ "GET /sessions/c1/messages": () => reply(200, [stored(1, "user", "hi")]) });

    await loadChat("c1");

    expect(getChat("c1")).toMatchObject({ loaded: true, loadError: null });
    expect(getChat("c1").messages).toEqual([stored(1, "user", "hi")]);
  });

  test("a chat nobody has opened is empty and not loaded, and is the same object each time", () => {
    expect(getChat("nobody")).toMatchObject({ messages: [], loaded: false });
    expect(getChat("nobody")).toBe(getChat("nobody"));
  });

  test("a failure is kept as its code", async () => {
    routes({
      "GET /sessions/c1/messages": () =>
        reply(404, { detail: { code: "session_not_found", message: "no such chat" } }),
    });

    await loadChat("c1");

    expect(getChat("c1")).toMatchObject({ loaded: true, loadError: "session_not_found" });
  });

  test("tells its listeners when the chat changes, and stops when they leave", async () => {
    routes({ "GET /sessions/c1/messages": () => reply(200, []) });
    const heard = vi.fn();
    const stop = subscribe(heard);

    await loadChat("c1");
    expect(heard).toHaveBeenCalled();

    heard.mockClear();
    stop();
    await loadChat("c1");
    expect(heard).not.toHaveBeenCalled();
  });
});

// A reply ----------------------------------------------------------------------------------------

describe("sending a message", () => {
  test("shows the question at once, then the memories and the text as they arrive", async () => {
    const live = liveReply();
    routes({
      "POST /sessions/c1/messages": () => live.response(),
      "GET /sessions/c1/messages": () => reply(200, []),
      "GET /sessions": () => reply(200, []),
    });

    const sent = await send("c1", "Where do I live?");

    expect(sent).toEqual({ ok: true });
    expect(getChat("c1").messages.map((m) => m.text)).toEqual(["Where do I live?"]);
    expect(isBusy(getChat("c1"))).toBe(true);

    live.push("memory", MEMORY);
    await vi.waitFor(() => expect(getChat("c1").turn?.memories).toHaveLength(1));
    expect(getChat("c1").turn?.text).toBe("");

    live.push("text", { text: "You live " });
    live.push("text", { text: "in Porto." });
    await vi.waitFor(() => expect(getChat("c1").turn?.text).toBe("You live in Porto."));
    live.end();
  });

  test("when it is done the reply is a message, and the history is read to settle it", async () => {
    const live = liveReply();
    const server = [
      stored(7, "user", "Where do I live?"),
      {
        ...stored(41, "assistant", "In Porto."),
        memories: [{ id: 1, kind: "fact", content: "x", past: false, status: "active" as const }],
      },
    ];
    routes({
      "POST /sessions/c1/messages": () => live.response(),
      "GET /sessions/c1/messages": () => reply(200, server),
      "GET /sessions": () => reply(200, [summary("c1", "Where do I live?", 2)]),
    });

    await send("c1", "Where do I live?");
    live.push("memory", MEMORY);
    live.push("text", { text: "In Porto." });
    live.push("done", DONE);
    live.end();

    await vi.waitFor(() => expect(getChat("c1").messages.map((m) => m.id)).toEqual([7, 41]));
    expect(getChat("c1").turn).toBeNull();
    expect(getChat("c1").messages).toEqual(server);
    expect(getChatList().items?.[0].message_count).toBe(2);
  });

  test("a turn that fails keeps its error, drops the half reply, and shows the stored question", async () => {
    const live = liveReply();
    routes({
      "POST /sessions/c1/messages": () => live.response(),
      "GET /sessions/c1/messages": () => reply(200, [stored(7, "user", "q")]),
      "GET /sessions": () => reply(200, []),
    });

    await send("c1", "q");
    live.push("text", { text: "half an ans" });
    live.push("error", { code: "model_error", message: "the model could not answer" });
    live.end();

    await vi.waitFor(() => expect(getChat("c1").messages.map((m) => m.id)).toEqual([7]));
    expect(getChat("c1").turn).toMatchObject({
      status: "failed",
      text: "",
      error: { code: "model_error" },
    });
    expect(isBusy(getChat("c1"))).toBe(false);
  });

  test("a connection that drops is a failed turn, not a hang", async () => {
    const live = liveReply();
    routes({
      "POST /sessions/c1/messages": () => live.response(),
      "GET /sessions/c1/messages": () => reply(200, [stored(7, "user", "q")]),
      "GET /sessions": () => reply(200, []),
    });

    await send("c1", "q");
    live.push("text", { text: "par" });
    live.end(); // no done, no error

    await vi.waitFor(() => expect(getChat("c1").turn?.status).toBe("failed"));
    expect(getChat("c1").turn?.error?.code).toBe("connection_lost");
  });

  test("cannot send while a reply is still arriving", async () => {
    const live = liveReply();
    routes({ "POST /sessions/c1/messages": () => live.response() });
    await send("c1", "first");

    const second = await send("c1", "second");

    expect(second).toMatchObject({ ok: false, error: { code: "turn_in_progress" } });
    expect(calls("POST /sessions/c1/messages")).toHaveLength(1);
    expect(getChat("c1").messages.map((m) => m.text)).toEqual(["first"]);
    live.end();
  });

  test("the reply keeps arriving with nobody watching", async () => {
    const live = liveReply();
    routes({
      "POST /sessions/c1/messages": () => live.response(),
      "GET /sessions/c1/messages": () => reply(200, []),
      "GET /sessions": () => reply(200, []),
    });
    await send("c1", "q");
    // No component is subscribed: the store alone reads the stream

    live.push("text", { text: "still " });
    live.push("text", { text: "here" });

    await vi.waitFor(() => expect(getChat("c1").turn?.text).toBe("still here"));
    live.end();
  });

  test("a second message sent right after the first reply is not wiped by the settling read", async () => {
    const live = liveReply();
    const next = liveReply();
    let release: (value: Response) => void = () => {};
    let post = 0;
    routes({
      "POST /sessions/c1/messages": () => (post++ === 0 ? live.response() : next.response()),
      "GET /sessions/c1/messages": () => new Promise<Response>((resolve) => (release = resolve)),
      "GET /sessions": () => reply(200, []),
    });
    await send("c1", "one");
    live.push("text", { text: "a" });
    live.push("done", DONE);
    live.end();
    await vi.waitFor(() => expect(getChat("c1").turn).toBeNull()); // done; the history read is pending

    await send("c1", "two");
    release(reply(200, [stored(1, "user", "one"), stored(41, "assistant", "a")])); // stale for "two"

    await vi.waitFor(() => expect(calls("GET /sessions").length).toBeGreaterThan(0));
    expect(getChat("c1").messages.map((m) => m.text)).toContain("two");
    expect(isBusy(getChat("c1"))).toBe(true);
    next.end();
  });
});

// A message the server will not take ---------------------------------------------------------------

describe("a refused message", () => {
  test.each([
    [409, "chat_full"],
    [413, "message_too_long"],
    [422, "empty_message"],
    [404, "session_not_found"],
  ])("a %i %s leaves the chat as it was and gives the error back", async (status, code) => {
    routes({
      "GET /sessions/c1/messages": () => reply(200, [stored(1, "user", "hi")]),
      "POST /sessions/c1/messages": () => reply(status, { detail: { code, message: "refused" } }),
    });
    await loadChat("c1");
    const before = getChat("c1");

    const result = await send("c1", "more");

    expect(result).toMatchObject({ ok: false, error: { code } });
    expect(getChat("c1").messages).toEqual(before.messages);
    expect(getChat("c1").turn).toBeNull();
  });

  test("a chat that is still answering is read again once, a few seconds later", async () => {
    vi.useFakeTimers();
    let reads = 0;
    routes({
      "GET /sessions/c1/messages": () => {
        reads++;
        return reply(200, [stored(1, "user", "q"), stored(2, "assistant", "a")]);
      },
      "POST /sessions/c1/messages": () =>
        reply(409, { detail: { code: "turn_in_progress", message: "still answering" } }),
    });

    await send("c1", "again");
    await send("c1", "and again");
    expect(reads).toBe(0);

    await vi.advanceTimersByTimeAsync(2999);
    expect(reads).toBe(0);
    await vi.advanceTimersByTimeAsync(2);
    expect(reads).toBe(1);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(reads).toBe(1);
    expect(getChat("c1").messages).toHaveLength(2);
  });

  test("an unreachable server is a network error", async () => {
    routes({});
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const result = await send("c1", "hi");

    expect(result).toMatchObject({ ok: false, error: { code: "network" } });
    expect(getChat("c1").messages).toEqual([]);
  });
});

// A new chat -----------------------------------------------------------------------------------------

describe("starting a chat", () => {
  test("creates it, sends the first message, and hands back the id while the reply streams", async () => {
    const live = liveReply();
    routes({
      "POST /sessions": () => reply(201, summary("new1")),
      "POST /sessions/new1/messages": () => live.response(),
      "GET /sessions/new1/messages": () => reply(200, []),
      "GET /sessions": () => reply(200, []),
    });

    const started = await startChat("Hello");

    expect(started).toEqual({ ok: true, id: "new1" });
    expect(getChat("new1").messages.map((m) => m.text)).toEqual(["Hello"]);
    expect(isBusy(getChat("new1"))).toBe(true);
    expect(getChatList().items?.[0].id).toBe("new1");
    live.push("text", { text: "Hi!" });
    await vi.waitFor(() => expect(getChat("new1").turn?.text).toBe("Hi!"));
    live.end();
  });

  test("a first message the server refuses is reported, with nothing shown", async () => {
    routes({
      "POST /sessions": () => reply(201, summary("new1")),
      "POST /sessions/new1/messages": () =>
        reply(503, { detail: "the agent is not configured" }),
    });

    const started = await startChat("Hello");

    expect(started).toMatchObject({ ok: false, error: { code: "agent_not_configured" } });
    expect(getChat("new1").messages).toEqual([]);
  });

  test("a chat that cannot even be created is reported", async () => {
    routes({ "POST /sessions": () => reply(500, "boom") });

    expect(await startChat("Hello")).toMatchObject({ ok: false, error: { code: "server_error" } });
    expect(calls("POST /sessions/new1/messages")).toHaveLength(0);
  });
});

// The chat list -----------------------------------------------------------------------------------------

describe("the chat list", () => {
  test("is kept as the server sends it", async () => {
    routes({ "GET /sessions": () => reply(200, [summary("b", "second", 2), summary("a", "first", 4)]) });

    await refreshChatList();

    expect(getChatList()).toEqual({
      items: [summary("b", "second", 2), summary("a", "first", 4)],
      error: null,
    });
  });

  test("a failure keeps the chats already shown and says what went wrong", async () => {
    routes({ "GET /sessions": () => reply(200, [summary("a")]) });
    await refreshChatList();
    routes({ "GET /sessions": () => reply(500, "boom") });

    await refreshChatList();

    expect(getChatList().items).toEqual([summary("a")]);
    expect(getChatList().error).toBe("server_error");
  });
});

// Signing out -------------------------------------------------------------------------------------------

test("signing out forgets every chat, the list and the waiting reload", async () => {
  vi.useFakeTimers();
  routes({
    "GET /sessions/c1/messages": () => reply(200, [stored(1, "user", "private")]),
    "GET /sessions": () => reply(200, [summary("c1", "private", 1)]),
    "POST /sessions/c1/messages": () =>
      reply(409, { detail: { code: "turn_in_progress", message: "x" } }),
  });
  await loadChat("c1");
  await refreshChatList();
  await send("c1", "more");

  resetStore();
  await vi.advanceTimersByTimeAsync(10_000);

  expect(getChat("c1").messages).toEqual([]);
  expect(getChatList().items).toBeNull();
  expect(calls("GET /sessions/c1/messages")).toHaveLength(1); // the reload never fired
});
