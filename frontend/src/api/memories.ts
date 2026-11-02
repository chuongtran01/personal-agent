import { request } from "./http";
import type { Memory, MemoryHistoryEntry, MemoryStatus } from "./types";

export interface MemoryQuery {
  /** omitted for every status */
  status?: MemoryStatus;
  limit?: number;
  offset?: number;
}

/** The memory list, newest first. */
export async function listMemories({
  status,
  limit = 100,
  offset = 0,
}: MemoryQuery = {}): Promise<Memory[]> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) query.set("status", status);
  return (await request(`/memories?${query}`)).json();
}

/** Mark a memory deleted: it is never recalled again. Deleting one that is already deleted is fine. */
export async function deleteMemory(id: number): Promise<void> {
  await request(`/memories/${id}`, { method: "DELETE" });
}

/** What happened to one memory, oldest first. */
export async function getMemoryHistory(id: number): Promise<MemoryHistoryEntry[]> {
  return (await request(`/memories/${id}/events`)).json();
}
