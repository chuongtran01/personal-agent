import { Suspense } from "react";

import { ChatRoute } from "@/components/chat-route";
import { ChatSkeleton } from "@/components/chat-view";

// The id is runtime data (Cache Components), so the part that reads it waits under a boundary
export default function ChatPage({ params }: PageProps<"/chats/[id]">) {
  return (
    <Suspense fallback={<ChatSkeleton />}>
      <ChatRoute params={params} />
    </Suspense>
  );
}
