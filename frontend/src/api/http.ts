import { clearToken, getToken } from "@/auth/token";

// The machinery every endpoint module shares: the /api prefix, the bearer token, and failures as
// an ApiError with a stable code. The endpoints themselves live in auth.ts, chats.ts and
// memories.ts.

// The browser talks to its own origin; Next forwards /api to the backend (next.config.ts)
const BASE = "/api";

/** A failed call, as a status and a stable code. Wording for the owner is not decided here. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let onUnauthorized: (() => void) | null = null;

/** What to do when the token is missing or refused: the app sends the owner to login. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

// The status codes that carry no code of their own in the body
function codeFor(status: number): string {
  if (status === 401) return "unauthorized";
  if (status === 404) return "not_found";
  if (status === 422) return "invalid_request";
  if (status === 503) return "unavailable";
  return status >= 500 ? "server_error" : "http_error";
}

/** The backend's errors are `{detail: {code, message}}`, or a plain string, or a list (422). */
async function errorFrom(response: Response): Promise<ApiError> {
  let detail: unknown;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    detail = undefined;
  }
  if (typeof detail === "object" && detail !== null && !Array.isArray(detail)) {
    const { code, message } = detail as { code?: unknown; message?: unknown };
    if (typeof code === "string") {
      return new ApiError(response.status, code, typeof message === "string" ? message : code);
    }
  }
  const message = typeof detail === "string" ? detail : `Request failed (${response.status})`;
  return new ApiError(response.status, codeFor(response.status), message);
}

export interface Options {
  method?: string;
  body?: unknown;
  /** false for login, where a 401 means a wrong password and not an expired token */
  auth?: boolean;
  signal?: AbortSignal;
}

/** One call to the backend. Throws an ApiError for any failure; a returned Response is ok. */
export async function request(
  path: string,
  { method = "GET", body, auth = true, signal }: Options = {},
): Promise<Response> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  if (auth) {
    const token = getToken();
    if (token) headers.authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "network", "Cannot reach the server");
  }

  if (!response.ok) {
    const error = await errorFrom(response);
    if (response.status === 401 && auth) {
      clearToken();
      onUnauthorized?.();
    }
    throw error;
  }
  return response;
}

/** The same call with one failure renamed, for where a status means something specific. */
export async function renaming<T>(
  status: number,
  code: string,
  call: () => Promise<T>,
): Promise<T> {
  try {
    return await call();
  } catch (error) {
    if (error instanceof ApiError && error.status === status) {
      throw new ApiError(status, code, error.message);
    }
    throw error;
  }
}
