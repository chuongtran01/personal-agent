import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";

import type { MemoryHistoryEntry } from "@/api/types";
import { setToken } from "@/auth/token";
import { fakeFetch, reply } from "@/test/fetch";

import { useMemoryHistory } from "./use-memory-history";

const { fetchMock } = fakeFetch();

const entry = (id: number, overrides: Partial<MemoryHistoryEntry> = {}): MemoryHistoryEntry => ({
  id,
  event: "created",
  reason: "the owner said it",
  message_id: null,
  created_at: "2026-08-01T00:00:00Z",
  ...overrides,
});

beforeEach(() => setToken("abc", 600));

describe("useMemoryHistory", () => {
  test("asks for nothing until it is told to", () => {
    const { result } = renderHook(() => useMemoryHistory(7));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.entries).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  test("reads the history of its memory when asked, and shows it was loading meanwhile", async () => {
    let answer: (response: Response) => void = () => {};
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => (answer = resolve)));
    const { result } = renderHook(() => useMemoryHistory(7));

    act(() => result.current.load());
    await waitFor(() => expect(result.current.loading).toBe(true));
    answer(reply(200, [entry(1), entry(2, { event: "deleted" })]));

    await waitFor(() => expect(result.current.entries).toHaveLength(2));
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/memories/7/events");
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  test("a second ask while one is on its way does not send another", async () => {
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    const { result } = renderHook(() => useMemoryHistory(7));

    act(() => {
      result.current.load();
      result.current.load();
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  test("a failure gives the code, keeps nothing, and asking again can succeed", async () => {
    fetchMock.mockResolvedValueOnce(
      reply(404, {
        detail: { code: "memory_not_found", message: "no memory 7" },
      }),
    );
    const { result } = renderHook(() => useMemoryHistory(7));

    act(() => result.current.load());
    await waitFor(() => expect(result.current.error).toBe("memory_not_found"));
    expect(result.current.entries).toBeNull();

    fetchMock.mockResolvedValueOnce(reply(200, [entry(1)]));
    act(() => result.current.load());

    await waitFor(() => expect(result.current.entries).toHaveLength(1));
    expect(result.current.error).toBeNull();
  });

  test("a network failure is a code of its own", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const { result } = renderHook(() => useMemoryHistory(7));

    act(() => result.current.load());

    await waitFor(() => expect(result.current.error).toBe("network"));
  });

  test("a reset forgets what was read, so the next ask reads it again", async () => {
    fetchMock.mockResolvedValue(reply(200, [entry(1)]));
    const { result } = renderHook(() => useMemoryHistory(7));
    act(() => result.current.load());
    await waitFor(() => expect(result.current.entries).toHaveLength(1));

    act(() => result.current.reset());
    expect(result.current.entries).toBeNull();

    fetchMock.mockResolvedValue(reply(200, [entry(1), entry(2, { event: "deleted" })]));
    act(() => result.current.load());
    await waitFor(() => expect(result.current.entries).toHaveLength(2));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  test("a reset also forgets a failure", async () => {
    fetchMock.mockResolvedValue(reply(500, "boom"));
    const { result } = renderHook(() => useMemoryHistory(7));
    act(() => result.current.load());
    await waitFor(() => expect(result.current.error).not.toBeNull());

    act(() => result.current.reset());

    expect(result.current.error).toBeNull();
  });
});
