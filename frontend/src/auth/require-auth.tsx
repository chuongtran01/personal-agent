"use client";

import { useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";

import { setUnauthorizedHandler } from "@/api/http";
import { getToken } from "@/auth/token";
import { resetStore } from "@/lib/chat-store";

import { loginUrl } from "./redirect";

// Three answers, not two. The server has no sessionStorage, and React hydrates a page with the
// server's answer before it reads the browser's, so for that render the token is simply not known.
// Taking "not known" for "signed out" would send an owner who reloads a page to login, and the login
// page, seeing the token, would send them straight back.
type Auth = "unknown" | "signed-in" | "signed-out";

// The token changes only through login and logout, both of which navigate, so there is nothing to
// subscribe to.
const noSubscription = () => () => {};
const inBrowser = (): Auth => (getToken() === null ? "signed-out" : "signed-in");
const onServer = (): Auth => "unknown";

/**
 * Renders its children only for an owner with a token. Without one it sends them to login, to come
 * back to this page afterwards. It renders nothing until it has looked, so the signed-in pages
 * never flash for a visitor. The check is here and not in Next's proxy because the token lives in
 * sessionStorage, which a server cannot see; the API is what actually refuses a request without
 * a valid token.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const auth = useSyncExternalStore(noSubscription, inBrowser, onServer);

  useEffect(() => {
    if (auth === "unknown") return; // the server, or the render that hydrates what it sent
    const here = () => window.location.pathname + window.location.search;
    if (auth === "signed-out") {
      router.replace(loginUrl(here()));
      return;
    }
    // A token refused later (expired, or the server was reset) sends the owner back the same way
    setUnauthorizedHandler(() => {
      resetStore();
      router.replace(loginUrl(here()));
    });
    return () => setUnauthorizedHandler(null);
  }, [auth, router]);

  return auth === "signed-in" ? children : null;
}
