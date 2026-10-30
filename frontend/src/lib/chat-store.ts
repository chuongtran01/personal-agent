import { ApiError } from "@/api/http";
import { createChat, getMessages, listChats, sendMessage } from "@/api/chats";
import type { ChatSummary, StreamEvent } from "@/api/types";

import {
  EMPTY_CHAT,
  NEW_CHAT,
  applyEvent,
  isBusy,
  refuseTurn,
  startTurn,
  withHistory,
  withLoadError,
  type ChatState,
} from "./chat-state";

// Every open chat's state, kept outside React and keyed by chat id. A reply is read here, not by the
// page that sent the message, so leaving the page (a new chat moves to its own address, the owner
// opens another chat) never drops a reply that is still arriving. Components read it with
// useSyncExternalStore (use-chat.ts). Nothing here survives a reload: the server has the history.

export interface ChatListState {
  /** null until the first load */
  items: ChatSummary[] | null;
  error: string | null;
}

export type SendResult = { ok: true } | { ok: false; error: ApiError };

// A chat the server says is still answering is read again every few seconds until the reply is in,
// for about two minutes at most: longer than a turn can take, so a chat whose turn failed (and so
// will never get a reply) stops being read
const BUSY_POLL_MS = 3000;
const BUSY_POLL_LIMIT = 40;

const chats = new Map<string, ChatState>();
let list: ChatListState = { items: null, error: null };
const listeners = new Set<() => void>();
const polling = new Set<string>();
const pollTimers = new Map<string, ReturnType<typeof setTimeout>>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The same object until the chat changes, as useSyncExternalStore needs. */
export function getChat(id: string): ChatState {
  return chats.get(id) ?? EMPTY_CHAT;
}

export function getChatList(): ChatListState {
  return list;
}

function setChat(id: string, state: ChatState) {
  chats.set(id, state);
  emit();
}

/** Forget everything, for when the owner signs out: the next one must not see this chat's text. */
export function resetStore() {
  chats.clear();
  list = { items: null, error: null };
  polling.clear();
  pollTimers.forEach(clearTimeout);
  pollTimers.clear();
  emit();
}

const asApiError = (error: unknown) =>
  error instanceof ApiError ? error : new ApiError(0, "network", "Cannot reach the server");

// The chat list ---------------------------------------------------------------------------------

export async function refreshChatList(): Promise<void> {
  try {
    list = { items: await listChats(), error: null };
  } catch (error) {
    list = { items: list.items, error: asApiError(error).code };
  }
  emit();
}

// One chat's history -------------------------------------------------------------------------------

/** Read the history. A reply that is arriving is left alone: its stream keeps the chat up to date. */
export async function loadChat(id: string): Promise<void> {
  if (isBusy(getChat(id))) return;
  try {
    const messages = await getMessages(id);
    if (!isBusy(getChat(id))) setChat(id, withHistory(getChat(id), messages));
  } catch (error) {
    setChat(id, withLoadError(getChat(id), asApiError(error).code));
  }
}

/** Replace what the browser guessed with what the server stored: real ids, memories as they stand now. */
async function settle(id: string) {
  await loadChat(id);
  await refreshChatList();
}

function stopPolling(id: string) {
  polling.delete(id);
  clearTimeout(pollTimers.get(id));
  pollTimers.delete(id);
}

/** Read the history every few seconds until the reply, that another window or a reload left running, is in. */
function pollUntilAnswered(id: string) {
  // No second poll can start: send() stops any poll before it runs, and refuses to run twice at once
  polling.add(id);
  let tries = 0;
  const tick = async () => {
    pollTimers.delete(id);
    tries += 1;
    await loadChat(id);
    if (!polling.has(id)) return; // stopped while reading: a new message, or signing out
    if (getChat(id).messages.at(-1)?.role === "assistant" || tries >= BUSY_POLL_LIMIT) {
      polling.delete(id);
      void refreshChatList();
      return;
    }
    pollTimers.set(id, setTimeout(tick, BUSY_POLL_MS));
  };
  pollTimers.set(id, setTimeout(tick, BUSY_POLL_MS));
}

// Sending ------------------------------------------------------------------------------------------

async function pump(id: string, turn: AsyncGenerator<StreamEvent>) {
  try {
    for await (const event of turn) setChat(id, applyEvent(getChat(id), event, new Date()));
  } catch {
    // The reader ends every turn with done or error and does not throw; this is only a last resort
    setChat(
      id,
      applyEvent(
        getChat(id),
        { type: "error", code: "connection_lost", message: "The connection was lost." },
        new Date(),
      ),
    );
  } finally {
    await settle(id);
  }
}

/**
 * Send a message. Resolves when the server has taken it or refused it; the reply then keeps
 * arriving in the background and shows up in the chat's state. A refused message leaves the chat as
 * it was, and the owner's text is theirs to put back.
 */
export async function send(id: string, text: string): Promise<SendResult> {
  const before = getChat(id);
  if (isBusy(before)) return { ok: false, error: new ApiError(409, "turn_in_progress", "still answering") };

  stopPolling(id); // the owner is sending now; a refusal starts the polling again
  setChat(id, startTurn(before, text, new Date()));
  let turn: AsyncGenerator<StreamEvent>;
  try {
    turn = await sendMessage(id, text);
  } catch (error) {
    setChat(id, refuseTurn(getChat(id)));
    const refusal = asApiError(error);
    if (refusal.code === "turn_in_progress") pollUntilAnswered(id);
    return { ok: false, error: refusal };
  }
  void pump(id, turn);
  return { ok: true };
}

/**
 * A new chat is created by its first message, so empty chats do not pile up. Resolves with the id
 * once the message is taken; the caller then moves to /chats/{id} while the reply streams.
 */
export async function startChat(text: string): Promise<{ ok: true; id: string } | { ok: false; error: ApiError }> {
  let created: ChatSummary;
  try {
    created = await createChat();
  } catch (error) {
    return { ok: false, error: asApiError(error) };
  }
  chats.set(created.id, NEW_CHAT);
  list = { items: [created, ...(list.items ?? [])], error: list.error };
  emit();

  const sent = await send(created.id, text);
  return sent.ok ? { ok: true, id: created.id } : sent;
}
