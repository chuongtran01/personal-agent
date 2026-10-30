import { Suspense } from "react";

import { ChatRoute } from "@/components/chat-route";
import { ChatSkeleton } from "@/components/chat-view";

// The signed-in layout renders nothing on the server (RequireAuth decides in the browser, where the
// token is), so Next cannot check this page for instant navigation. See docs/PHASE4.md.
export const instant = false;

// The id is runtime data (Cache Components), so the part that reads it waits under a boundary
export default function ChatPage({ params }: PageProps<"/chats/[id]">) {
  return (
    <Suspense fallback={<ChatSkeleton />}>
      <ChatRoute params={params} />
    </Suspense>
  );
}
