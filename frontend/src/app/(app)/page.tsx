import { NewChat } from "@/components/new-chat";

// The signed-in layout renders nothing on the server (RequireAuth decides in the browser, where the
// token is), so Next cannot check this page for instant navigation. The page is a client-side
// transition all the same; see docs/PHASE4.md ("Signing in").
export const instant = false;

export default function NewChatPage() {
  return <NewChat />;
}
