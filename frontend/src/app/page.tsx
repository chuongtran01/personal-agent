import { Button } from "@/components/ui/button";

// Replaced by the pages (login, chats, memories) as they are built
export default function Home() {
  return (
    <main className="mx-auto flex min-h-svh max-w-md flex-col items-center justify-center gap-4 p-4">
      <h1 className="text-xl font-medium">Personal Agent</h1>
      <Button>Hello</Button>
    </main>
  );
}
