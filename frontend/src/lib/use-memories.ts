"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { listMemories } from "@/api/memories";
import { ApiError } from "@/api/http";
import type { Memory, MemoryStatus } from "@/api/types";

import { PAGE_SIZE } from "./memory-view";

export interface MemoryPages {
  rows: Memory[];
  /** a page is on its way */
  loading: boolean;
  /** the code of the failure that stopped the last page, if one did */
  error: string | null;
  /** the server has no more to give: the last page came back short */
  done: boolean;
  /** the first page has been asked for */
  started: boolean;
  loadMore: () => void;
}

/**
 * One tab's memories, a page at a time. The first page is asked for when the tab is first shown, and
 * what was loaded stays, so going back to a tab does not ask again.
 */
export function useMemoryPages(status: MemoryStatus | undefined, active: boolean): MemoryPages {
  const [rows, setRows] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [started, setStarted] = useState(false);
  const inFlight = useRef(false); // a second click, or a second effect run, must not ask twice
  const count = useRef(0);

  const loadMore = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setStarted(true);
    setLoading(true);
    setError(null);
    try {
      const page = await listMemories({ status, limit: PAGE_SIZE, offset: count.current });
      count.current += page.length;
      setRows((current) => [...current, ...page]);
      setDone(page.length < PAGE_SIZE);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.code : "network");
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    if (active && !started) void loadMore();
  }, [active, started, loadMore]);

  return { rows, loading, error, done, started, loadMore: () => void loadMore() };
}
