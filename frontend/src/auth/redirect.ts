// Where to send the owner after login, and the login URL that remembers where they were headed.
// The `next` value comes from the address bar, so it is untrusted: it is only ever followed when it
// is a path on this site. `router.replace` runs `javascript:` URLs, and `//host` is another site.

const FALLBACK = "/";
const ORIGIN = "http://same-origin.invalid";

const isLogin = (path: string) => path === "/login" || path.startsWith("/login/") || path.startsWith("/login?");

/** The path to go to after login: `next` when it is a path on this site, otherwise the home page. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return FALLBACK;
  let url: URL;
  try {
    url = new URL(next, ORIGIN);
  } catch {
    return FALLBACK;
  }
  // The URL parser drops tabs and newlines, which can turn "/\t/host" into "//host"
  if (url.origin !== ORIGIN) return FALLBACK;
  const path = url.pathname + url.search;
  return isLogin(path) ? FALLBACK : path;
}

/** The marker a refused token leaves in the login address, so the page can say why the owner is there. */
export const EXPIRED = "expired";

/** Whether the login address says the owner's session ended. Only the one known word counts. */
export function sessionEnded(search: string): boolean {
  return new URLSearchParams(search).get("reason") === EXPIRED;
}

/**
 * The login page, told where to come back to. The home page and the login page itself need no
 * memory. `expired` is for a token the server refused: someone who never signed in has no session to
 * say ended.
 */
export function loginUrl(from: string, { expired = false }: { expired?: boolean } = {}): string {
  const target = safeNext(from);
  const query = new URLSearchParams();
  if (target !== FALLBACK) query.set("next", target);
  if (expired) query.set("reason", EXPIRED);
  const text = query.toString();
  return text ? `/login?${text}` : "/login";
}
