"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";

import { login } from "@/api/auth";
import { ApiError } from "@/api/http";
import { safeNext, sessionEnded } from "@/auth/redirect";
import { getToken } from "@/auth/token";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { messageFor } from "@/lib/messages";

const noSubscription = () => () => {};

const where = () => safeNext(new URLSearchParams(window.location.search).get("next"));

export function LoginForm() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A token the server refused sends the owner here with a marker; it says why they are back. The
  // address is only known in the browser, so the server and the first render say "not ended"
  const ended = useSyncExternalStore(noSubscription, () => sessionEnded(window.location.search), () => false);

  // Someone who is already signed in has no use for the form
  useEffect(() => {
    if (getToken()) router.replace(where());
  }, [router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || !password) return;
    setPending(true);
    setError(null);
    try {
      await login(password);
      router.replace(where()); // stays pending: the page is on its way out
    } catch (failure) {
      setError(messageFor(failure instanceof ApiError ? failure.code : "unknown"));
      setPending(false);
      input.current?.focus();
      input.current?.select();
    }
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Personal Agent</CardTitle>
        <CardDescription>Enter the password to continue.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              ref={input}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-invalid={error ? true : undefined}
            />
          </div>
          {ended && !error && (
            <Alert>
              <AlertDescription>{messageFor("unauthorized")}</AlertDescription>
            </Alert>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button type="submit" disabled={pending || !password}>
            {pending ? "Logging in…" : "Log in"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
