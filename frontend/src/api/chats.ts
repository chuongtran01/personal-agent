import { ApiError, renaming, request } from "./http";
import { readEvents } from "./sse";
import type { ChatMessage, ChatSummary, StreamEvent } from "./types";

const chatPath = (chatId: string) => `/sessions/${encodeURIComponent(chatId)}`;

export async function createChat(): Promise<ChatSummary> {
  return (await request("/sessions", { method: "POST" })).json();
}

/** Newest first. */
export async function listChats(limit = 50): Promise<ChatSummary[]> {
  return (await request(`/sessions?limit=${limit}`)).json();
}

/** The owner's messages and the agent's answers, each reply with the memories recalled for it. */
export async function getMessages(chatId: string): Promise<ChatMessage[]> {
  return (await request(`${chatPath(chatId)}/messages`)).json();
}

/**
 * Send a message. A message the server cannot take (empty, too long, no such chat, chat full, a
 * reply still coming) throws an ApiError here, before any stream; once it returns, the turn is
 * accepted and its events arrive from the iterator, ending with `done` or `error`.
 */
export async function sendMessage(
  chatId: string,
  text: string,
  signal?: AbortSignal,
): Promise<AsyncGenerator<StreamEvent>> {
  const response = await renaming(503, "agent_not_configured", () =>
    request(`${chatPath(chatId)}/messages`, { method: "POST", body: { text }, signal }),
  );
  if (!response.body) throw new ApiError(0, "bad_stream", "The reply could not be read.");
  return readEvents(response.body);
}
