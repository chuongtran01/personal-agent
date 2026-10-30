import type { ChatMessage, MemoryRef, RecalledMemory, StreamEvent } from "@/api/types";

// What one chat holds while it is open, and how a turn's events change it. Pure functions, no
// React and no network, so the streaming rules are tested on their own (chat-store.ts runs them).

/** The reply being read. It is shown under the question until it is complete or has failed. */
export interface ActiveTurn {
  /** from the `memory` event, which arrives before the first word */
  memories: RecalledMemory[] | null;
  text: string;
  status: "streaming" | "failed";
  error: { code: string; message: string } | null;
}

export interface ChatState {
  messages: ChatMessage[];
  /** the history has been fetched, or the chat is new and has none */
  loaded: boolean;
  /** the code of the failure that stopped the history loading */
  loadError: string | null;
  turn: ActiveTurn | null;
}

export const EMPTY_CHAT: ChatState = { messages: [], loaded: false, loadError: null, turn: null };
export const NEW_CHAT: ChatState = { ...EMPTY_CHAT, loaded: true };

/** The recalled memories as the live `memory` event gives them, in the shape a reloaded chat has.
 *  Their status is settled when the history is read again after the reply. */
export function recalledFrom(current: MemoryRef[], past: MemoryRef[]): RecalledMemory[] {
  const shape = (memory: MemoryRef, wasTrue: boolean): RecalledMemory => ({
    id: memory.id,
    kind: memory.kind,
    content: memory.content,
    past: wasTrue,
    status: "active",
  });
  return [...current.map((m) => shape(m, false)), ...past.map((m) => shape(m, true))];
}

const isStreaming = (state: ChatState) => state.turn?.status === "streaming";

export function isBusy(state: ChatState): boolean {
  return isStreaming(state);
}

/** The owner's message goes in at once, with a temporary negative id until the history is read. */
export function startTurn(state: ChatState, text: string, now: Date): ChatState {
  const lowest = Math.min(0, ...state.messages.map((m) => m.id));
  const question: ChatMessage = {
    id: lowest - 1,
    role: "user",
    text,
    created_at: now.toISOString(),
    memories: [],
  };
  return {
    ...state,
    messages: [...state.messages, question],
    turn: { memories: null, text: "", status: "streaming", error: null },
  };
}

/** The server would not take the message: it was never stored, so it leaves the chat. */
export function refuseTurn(state: ChatState): ChatState {
  const last = state.messages.at(-1);
  const messages = last && last.id < 0 ? state.messages.slice(0, -1) : state.messages;
  return { ...state, messages, turn: null };
}

export function applyEvent(state: ChatState, event: StreamEvent, now: Date): ChatState {
  const turn = state.turn;
  if (!turn || turn.status !== "streaming") return state;

  switch (event.type) {
    case "memory":
      return { ...state, turn: { ...turn, memories: recalledFrom(event.current, event.past) } };
    case "text":
      return { ...state, turn: { ...turn, text: turn.text + event.text } };
    case "done":
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            id: event.message_id,
            role: "assistant",
            text: turn.text,
            created_at: now.toISOString(),
            memories: turn.memories ?? [],
          },
        ],
        turn: null,
      };
    case "error":
      // What was streamed is dropped: a failed turn stores no reply, and half an answer misleads
      return {
        ...state,
        turn: { memories: turn.memories, text: "", status: "failed", error: { code: event.code, message: event.message } },
      };
  }
}

/** The history as the server has it. A turn still being read keeps its place after it. */
export function withHistory(state: ChatState, messages: ChatMessage[]): ChatState {
  return { ...state, messages, loaded: true, loadError: null };
}

export function withLoadError(state: ChatState, code: string): ChatState {
  return { ...state, loaded: true, loadError: code };
}
