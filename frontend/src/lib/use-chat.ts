"use client";

import { useSyncExternalStore } from "react";

import { EMPTY_CHAT, type ChatState } from "./chat-state";
import { getChat, getChatList, subscribe, type ChatListState } from "./chat-store";

const EMPTY_LIST: ChatListState = { items: null, error: null };

export function useChat(id: string): ChatState {
  return useSyncExternalStore(
    subscribe,
    () => getChat(id),
    () => EMPTY_CHAT,
  );
}

export function useChatList(): ChatListState {
  return useSyncExternalStore(subscribe, getChatList, () => EMPTY_LIST);
}
