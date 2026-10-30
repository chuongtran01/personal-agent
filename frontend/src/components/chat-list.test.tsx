import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import type { ChatSummary } from "@/api/types";
import { setToken } from "@/auth/token";
import { resetStore } from "@/lib/chat-store";
import { callsTo, fakeFetch, reply, routeFetch } from "@/test/fetch";
import { nav } from "@/test/navigation";

import { ChatList } from "./chat-list";

vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

const { fetchMock } = fakeFetch();
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const chat = (id: string, preview: string | null, minutes: number): ChatSummary => ({
  id,
  created_at: ago(minutes),
  message_count: preview ? 2 : 0,
  preview,
});

beforeEach(() => {
  resetStore();
  nav.pathname = "/";
  setToken("abc", 600);
});

test("lists the chats as the server sends them, newest first, each a link to its chat", async () => {
  routeFetch(fetchMock, {
    "GET /sessions": () => reply(200, [chat("b", "Where do I live?", 5), chat("a", "My dentist", 180)]),
  });

  render(<ChatList />);

  const links = await screen.findAllByRole("link", { name: /Where do I live|My dentist/ });
  expect(links.map((l) => l.getAttribute("href"))).toEqual(["/chats/b", "/chats/a"]);
  expect(links[0]).toHaveTextContent("5 minutes ago");
  expect(links[1]).toHaveTextContent("3 hours ago");
});

test("a chat with no message yet is called New chat", async () => {
  routeFetch(fetchMock, { "GET /sessions": () => reply(200, [chat("a", null, 1)]) });

  render(<ChatList />);

  expect(await screen.findByRole("link", { name: /New chat.*1 minute ago/ })).toHaveAttribute("href", "/chats/a");
});

test("offers a new chat at the top", async () => {
  routeFetch(fetchMock, { "GET /sessions": () => reply(200, []) });

  render(<ChatList />);

  expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("href", "/");
  expect(await screen.findByText("No chats yet.")).toBeInTheDocument();
});

test("marks the chat that is open", async () => {
  nav.pathname = "/chats/a";
  routeFetch(fetchMock, { "GET /sessions": () => reply(200, [chat("b", "other", 5), chat("a", "this one", 9)]) });

  render(<ChatList />);

  expect(await screen.findByRole("link", { name: /this one/ })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: /other/ })).not.toHaveAttribute("aria-current");
});

test("shows placeholders while the chats load", () => {
  routeFetch(fetchMock, { "GET /sessions": () => new Promise<Response>(() => {}) });

  const { container } = render(<ChatList />);

  expect(container.querySelectorAll("[data-slot=skeleton]").length).toBeGreaterThan(0);
  expect(screen.queryByText("No chats yet.")).not.toBeInTheDocument();
});

test("a failure says so and can be tried again", async () => {
  routeFetch(fetchMock, { "GET /sessions": () => reply(500, "boom") });
  render(<ChatList />);
  expect(await screen.findByText("Could not load your chats.")).toBeInTheDocument();

  routeFetch(fetchMock, { "GET /sessions": () => reply(200, [chat("a", "back again", 1)]) });
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));

  expect(await screen.findByRole("link", { name: /back again/ })).toBeInTheDocument();
  expect(callsTo(fetchMock, "GET /sessions")).toHaveLength(2); // the failed load and the retry
});

test("tells its parent when a link is followed, so a drawer can close", async () => {
  routeFetch(fetchMock, { "GET /sessions": () => reply(200, [chat("a", "a chat", 1)]) });
  const onNavigate = vi.fn();
  render(<ChatList onNavigate={onNavigate} />);

  await userEvent.click(await screen.findByRole("link", { name: /a chat/ }));

  expect(onNavigate).toHaveBeenCalled();
});
