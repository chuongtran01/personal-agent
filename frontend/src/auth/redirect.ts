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

/** The login page, told where to come back to. The home page and the login page itself need no memory. */
export function loginUrl(from: string): string {
  const target = safeNext(from);
  return target === FALLBACK ? "/login" : `/login?next=${encodeURIComponent(target)}`;
}
