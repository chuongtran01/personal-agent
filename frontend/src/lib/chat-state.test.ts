import { describe, expect, test } from "vitest";

import type { ChatMessage, DoneEvent, MemoryRef, StreamEvent } from "@/api/types";

import {
  NEW_CHAT,
  applyEvent,
  isBusy,
  recalledFrom,
  refuseTurn,
  startTurn,
  withHistory,
  withLoadError,
  type ChatState,
} from "./chat-state";

const NOW = new Date("2026-10-30T10:00:00Z");
const ref = (id: number, content = `memory ${id}`, valid_until: string | null = null): MemoryRef => ({
  id,
  kind: "fact",
  content,
  valid_from: null,
  valid_until,
});
const DONE: DoneEvent = {
  type: "done",
  message_id: 41,
  input_tokens: 10,
  output_tokens: 4,
  steps: 1,
  recall_seconds: 0.1,
  total_seconds: 1,
};
const play = (state: ChatState, ...events: StreamEvent[]) =>
  events.reduce((s, e) => applyEvent(s, e, NOW), state);
const stored = (id: number, role: ChatMessage["role"], text: string): ChatMessage => ({
  id,
  role,
  text,
  created_at: "2026-10-30T09:00:00Z",
  memories: [],
});

describe("a turn", () => {
  test("puts the question in at once and starts waiting", () => {
    const state = startTurn(NEW_CHAT, "Where do I live?", NOW);

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({ role: "user", text: "Where do I live?", memories: [] });
    expect(state.messages[0].id).toBeLessThan(0);
    expect(state.turn).toEqual({ memories: null, text: "", status: "streaming", error: null });
    expect(isBusy(state)).toBe(true);
  });

  test("gives each question a temporary id of its own", () => {
    const first = startTurn(NEW_CHAT, "one", NOW);
    const second = startTurn(play(first, DONE), "two", NOW);

    const ids = second.messages.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  test("shows the recalled memories before the first word", () => {
    const state = play(
      startTurn(NEW_CHAT, "q", NOW),
      { type: "memory", current: [ref(1)], past: [ref(2, "old", "2026-03-01T00:00:00Z")] },
    );

    expect(state.turn?.memories).toEqual([
      { id: 1, kind: "fact", content: "memory 1", past: false, status: "active" },
      { id: 2, kind: "fact", content: "old", past: true, status: "active" },
    ]);
    expect(state.turn?.text).toBe("");
  });

  test("builds the reply from the text events, in order", () => {
    const state = play(
      startTurn(NEW_CHAT, "q", NOW),
      { type: "text", text: "You live " },
      { type: "text", text: "in Porto." },
    );

    expect(state.turn?.text).toBe("You live in Porto.");
    expect(isBusy(state)).toBe(true);
  });

  test("done turns the streamed reply into a message with the stored id and its memories", () => {
    const state = play(
      startTurn(NEW_CHAT, "Where do I live?", NOW),
      { type: "memory", current: [ref(1)], past: [] },
      { type: "text", text: "Porto." },
      DONE,
    );

    expect(state.turn).toBeNull();
    expect(isBusy(state)).toBe(false);
    expect(state.messages.at(-1)).toMatchObject({
      id: 41,
      role: "assistant",
      text: "Porto.",
      memories: [{ id: 1, past: false }],
    });
    expect(state.messages).toHaveLength(2);
  });

  test("a reply with no recalled memories has none", () => {
    const state = play(startTurn(NEW_CHAT, "q", NOW), { type: "text", text: "Hi." }, DONE);

    expect(state.messages.at(-1)?.memories).toEqual([]);
  });

  test("an error drops what was streamed and keeps the question", () => {
    const state = play(
      startTurn(NEW_CHAT, "q", NOW),
      { type: "memory", current: [ref(1)], past: [] },
      { type: "text", text: "half an ans" },
      { type: "error", code: "model_error", message: "the model could not answer" },
    );

    expect(state.turn).toMatchObject({
      status: "failed",
      text: "",
      error: { code: "model_error", message: "the model could not answer" },
    });
    expect(state.messages).toHaveLength(1);
    expect(isBusy(state)).toBe(false);
  });

  test("events after the turn has ended change nothing", () => {
    const ended = play(startTurn(NEW_CHAT, "q", NOW), { type: "text", text: "a" }, DONE);
    const failed = play(startTurn(NEW_CHAT, "q", NOW), {
      type: "error",
      code: "x",
      message: "y",
    });

    expect(play(ended, { type: "text", text: "late" })).toBe(ended);
    expect(play(failed, { type: "text", text: "late" })).toBe(failed);
    expect(applyEvent(NEW_CHAT, { type: "text", text: "stray" }, NOW)).toBe(NEW_CHAT);
  });

  test("does not change the state it was given", () => {
    const before = startTurn(NEW_CHAT, "q", NOW);
    const copy = structuredClone(before);

    play(before, { type: "text", text: "a" }, DONE);

    expect(before).toEqual(copy);
  });
});

describe("a refused message", () => {
  test("leaves the chat as it was: it was never stored", () => {
    const before = withHistory(NEW_CHAT, [stored(1, "user", "hi"), stored(2, "assistant", "hello")]);

    const after = refuseTurn(startTurn(before, "too long", NOW));

    expect(after).toEqual(before);
  });

  test("does not remove a stored message when there is no question waiting", () => {
    const before = withHistory(NEW_CHAT, [stored(1, "user", "hi")]);

    expect(refuseTurn(before).messages).toEqual(before.messages);
  });
});

describe("the history", () => {
  test("replaces the messages and marks the chat loaded", () => {
    const state = withHistory({ ...NEW_CHAT, loaded: false }, [stored(1, "user", "hi")]);

    expect(state).toMatchObject({ loaded: true, loadError: null });
    expect(state.messages).toHaveLength(1);
  });

  test("leaves a reply that is still arriving alone", () => {
    const streaming = play(startTurn(NEW_CHAT, "q", NOW), { type: "text", text: "so far" });

    const after = withHistory(streaming, [stored(7, "user", "q")]);

    expect(after.turn).toEqual(streaming.turn);
    expect(after.messages[0].id).toBe(7);
  });

  test("a failure to load is a code and the chat counts as loaded", () => {
    expect(withLoadError({ ...NEW_CHAT, loaded: false }, "network")).toMatchObject({
      loaded: true,
      loadError: "network",
    });
  });
});

describe("recalledFrom", () => {
  test("keeps the current memories first and marks the past ones", () => {
    const memories = recalledFrom([ref(1), ref(2)], [ref(3)]);

    expect(memories.map((m) => [m.id, m.past])).toEqual([
      [1, false],
      [2, false],
      [3, true],
    ]);
  });
});
