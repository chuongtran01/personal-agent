"use client";

import { useCallback, useRef, useState } from "react";

import { ApiError } from "@/api/http";
import { getMemoryHistory } from "@/api/memories";
import type { MemoryHistoryEntry } from "@/api/types";

export interface MemoryHistory {
  /** null until it has been read */
  entries: MemoryHistoryEntry[] | null;
  loading: boolean;
  /** the code of the failure, if reading failed */
  error: string | null;
  /** read it now, unless a read is already on its way */
  load: () => void;
  /** forget what was read, because the memory has changed since */
  reset: () => void;
}

/**
 * One memory's history. It is read when the owner opens it (the caller calls `load`), kept while the
 * row lives, and read again after a `reset`. A failure does not loop: opening it again asks again.
 */
export function useMemoryHistory(id: number): MemoryHistory {
  const [entries, setEntries] = useState<MemoryHistoryEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false); // a second click must not ask twice

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    setError(null);
    try {
      setEntries(await getMemoryHistory(id));
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.code : "network");
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [id]);

  const reset = useCallback(() => {
    setEntries(null);
    setError(null);
  }, []);

  return { entries, loading, error, load: () => void load(), reset };
}
