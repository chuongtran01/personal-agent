import { MemoryList } from "@/components/memory-list";

// The signed-in layout renders nothing on the server (RequireAuth decides in the browser, where the
// token is), so Next cannot check this page for instant navigation. See docs/PHASE4.md.
export const instant = false;

export default function MemoriesPage() {
  return <MemoryList />;
}
