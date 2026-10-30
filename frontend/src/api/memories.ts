import { request } from "./http";
import type { Memory, MemoryStatus } from "./types";

export interface MemoryQuery {
  /** omitted for every status */
  status?: MemoryStatus;
  limit?: number;
  offset?: number;
}

/** The memory list, newest first. Read-only: editing and deleting are phase 5. */
export async function listMemories({
  status,
  limit = 100,
  offset = 0,
}: MemoryQuery = {}): Promise<Memory[]> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) query.set("status", status);
  return (await request(`/memories?${query}`)).json();
}
