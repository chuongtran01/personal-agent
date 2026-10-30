import { afterEach, beforeEach, expect, vi } from "vitest";

import { ApiError, setUnauthorizedHandler } from "@/api/http";

/**
 * A fake `fetch` for the endpoint tests, with a clean session storage and no unauthorized handler
 * around each test. Call it once at the top of a test file.
 */
export function fakeFetch() {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setUnauthorizedHandler(null);
  });

  const lastCall = () => {
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    return { url: String(url), init: init as RequestInit };
  };

  return {
    fetchMock,
    lastCall,
    headers: () => lastCall().init.headers as Record<string, string>,
  };
}

export type Handler = (init?: RequestInit) => Response | Promise<Response>;

const keyOf = (input: RequestInfo | URL, init?: RequestInit) =>
  `${init?.method ?? "GET"} ${String(input).replace(/^\/api/, "").split("?")[0]}`;

/** Route fake fetch calls by "METHOD /path". Anything unrouted fails the test loudly. */
export function routeFetch(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, table: Record<string, Handler>) {
  fetchMock.mockImplementation(async (input, init) => {
    const handler = table[keyOf(input, init)];
    if (!handler) throw new Error(`unrouted: ${keyOf(input, init)}`);
    return handler(init);
  });
}

/** The calls made so far to one "METHOD /path". */
export const callsTo = (fetchMock: ReturnType<typeof vi.fn<typeof fetch>>, key: string) =>
  fetchMock.mock.calls.filter(([input, init]) => keyOf(input, init) === key);

/** A response with a JSON body, or a raw string body. */
export const reply = (status: number, body: unknown) =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), { status });

/** The ApiError a call fails with; fails the test if it fails any other way or not at all. */
export async function failure(call: Promise<unknown>): Promise<ApiError> {
  const error = await call.then(
    () => null,
    (e: unknown) => e,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}
