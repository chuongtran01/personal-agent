import { act, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { setToken } from "@/auth/token";
import { resetStore } from "@/lib/chat-store";
import { callsTo, fakeFetch, reply, routeFetch } from "@/test/fetch";

import { ChatRoute } from "./chat-route";

vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

const { fetchMock } = fakeFetch();

beforeEach(() => {
  resetStore();
  setToken("abc", 600);
});

test("opens the chat whose id is in the address", async () => {
  routeFetch(fetchMock, {
    "GET /sessions/abc-123/messages": () =>
      reply(200, [{ id: 1, role: "user", text: "from abc", created_at: "2026-10-30T09:00:00Z", memories: [] }]),
    "GET /sessions": () => reply(200, []),
  });

  await act(async () => {
    render(
      <Suspense fallback={<p>waiting for the address</p>}>
        <ChatRoute params={Promise.resolve({ id: "abc-123" })} />
      </Suspense>,
    );
  });

  expect(await screen.findByText("from abc")).toBeInTheDocument();
  expect(callsTo(fetchMock, "GET /sessions/abc-123/messages")).toHaveLength(1);
});

test("moving to another chat opens that chat, not the old one's text", async () => {
  routeFetch(fetchMock, {
    "GET /sessions/one/messages": () =>
      reply(200, [{ id: 1, role: "user", text: "first chat", created_at: "2026-10-30T09:00:00Z", memories: [] }]),
    "GET /sessions/two/messages": () =>
      reply(200, [{ id: 2, role: "user", text: "second chat", created_at: "2026-10-30T09:00:00Z", memories: [] }]),
    "GET /sessions": () => reply(200, []),
  });
  const open = (id: string) => (
    <Suspense fallback={null}>
      <ChatRoute params={Promise.resolve({ id })} />
    </Suspense>
  );
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(open("one"));
  });
  await screen.findByText("first chat");

  await act(async () => {
    view.rerender(open("two"));
  });

  expect(await screen.findByText("second chat")).toBeInTheDocument();
  expect(screen.queryByText("first chat")).not.toBeInTheDocument();
});
