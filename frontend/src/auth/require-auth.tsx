"use client";

import { useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";

import { setUnauthorizedHandler } from "@/api/http";
import { getToken } from "@/auth/token";
import { resetStore } from "@/lib/chat-store";

import { loginUrl } from "./redirect";

// The token changes only through login and logout, both of which navigate, so there is nothing to
// subscribe to. The server has no sessionStorage and so never sees a token.
const noSubscription = () => () => {};
const hasToken = () => getToken() !== null;
const onServer = () => false;

/**
 * Renders its children only for an owner with a token. Without one it sends them to login, to come
 * back to this page afterwards. It renders nothing until it has looked, so the signed-in pages
 * never flash for a visitor. The check is here and not in Next's proxy because the token lives in
 * sessionStorage, which a server cannot see; the API is what actually refuses a request without
 * a valid token.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const signedIn = useSyncExternalStore(noSubscription, hasToken, onServer);

  useEffect(() => {
    const here = () => window.location.pathname + window.location.search;
    if (!signedIn) {
      router.replace(loginUrl(here()));
      return;
    }
    // A token refused later (expired, or the server was reset) sends the owner back the same way
    setUnauthorizedHandler(() => {
      resetStore();
      router.replace(loginUrl(here()));
    });
    return () => setUnauthorizedHandler(null);
  }, [signedIn, router]);

  return signedIn ? children : null;
}
