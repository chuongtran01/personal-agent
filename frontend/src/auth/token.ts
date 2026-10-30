// The owner's token, kept for the tab: it survives a reload and is gone when the tab closes.
// Storage can be missing or throw (private windows, the server), so every access is guarded and
// the app works, signed out, without it.

const KEY = "personal-agent.token";

interface Stored {
  token: string;
  expiresAt: number;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** Keep a token until it expires, so an expired one is dropped without a failed request first. */
export function setToken(token: string, expiresInSeconds: number, now = Date.now()): void {
  const stored: Stored = { token, expiresAt: now + expiresInSeconds * 1000 };
  try {
    storage()?.setItem(KEY, JSON.stringify(stored));
  } catch {
    // nothing to keep it in: the next load asks for the password again
  }
}

export function getToken(now = Date.now()): string | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(KEY);
    if (!raw) return null;
    const stored = JSON.parse(raw) as Partial<Stored>;
    if (typeof stored.token !== "string" || typeof stored.expiresAt !== "number") {
      clearToken();
      return null;
    }
    if (stored.expiresAt <= now) {
      clearToken();
      return null;
    }
    return stored.token;
  } catch {
    clearToken();
    return null;
  }
}

export function clearToken(): void {
  try {
    storage()?.removeItem(KEY);
  } catch {
    // already gone
  }
}
