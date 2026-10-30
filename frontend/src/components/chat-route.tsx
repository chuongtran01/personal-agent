"use client";

import { use } from "react";

import { ChatView } from "@/components/chat-view";

/** /chats/{id}. The id is runtime data, so this sits under a Suspense boundary in the page. */
export function ChatRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ChatView key={id} chatId={id} />;
}
