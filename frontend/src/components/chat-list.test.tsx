import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import type { ChatSummary } from "@/api/types";
import { setToken } from "@/auth/token";
import { resetStore } from "@/lib/chat-store";
import { callsTo, fakeFetch, reply, routeFetch } from "@/test/fetch";
import { nav } from "@/test/navigation";
import { renderInSidebar } from "@/test/sidebar";

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

  renderInSidebar(<ChatList />);

  const links = await screen.findAllByRole("link", { name: /Where do I live|My dentist/ });
  expect(links.map((l) => l.getAttribute("href"))).toEqual(["/chats/b", "/chats/a"]);
  expect(screen.getByText("Recents")).toBeInTheDocument();
});

test("the time a chat was started is a hover title, not part of its name", async () => {
  routeFetch(fetchMock, {
    "GET /sessions": () => reply(200, [chat("b", "Where do I live?", 5), chat("a", "My dentist", 180)]),
  });

  renderInSidebar(<ChatList />);

  expect(await screen.findByRole("link", { name: "Where do I live?" })).toHaveAttribute("title", "5 minutes ago");
  expect(screen.getByRole("link", { name: "My dentist" })).toHaveAttribute("title", "3 hours ago");
});

test("a chat with no message yet is called New chat", async () => {
  routeFetch(fetchMock, { "GET /sessions": () => reply(200, [chat("a", null, 1)]) });

  renderInSidebar(<ChatList />);

  expect(await screen.findByRole("link", { name: "New chat" })).toHaveAttribute("href", "/chats/a");
});

test("says so when there are no chats yet", async () => {
  routeFetch(fetchMock, { "GET /sessions": () => reply(200, []) });

  renderInSidebar(<ChatList />);

  expect(await screen.findByText("No chats yet.")).toBeInTheDocument();
});

test("marks the chat that is open", async () => {
  nav.pathname = "/chats/a";
  routeFetch(fetchMock, { "GET /sessions": () => reply(200, [chat("b", "other", 5), chat("a", "this one", 9)]) });

  renderInSidebar(<ChatList />);

  expect(await screen.findByRole("link", { name: /this one/ })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: /other/ })).not.toHaveAttribute("aria-current");
});

test("shows placeholders while the chats load", () => {
  routeFetch(fetchMock, { "GET /sessions": () => new Promise<Response>(() => {}) });

  const { container } = renderInSidebar(<ChatList />);

  expect(container.querySelectorAll("[data-slot=skeleton]").length).toBeGreaterThan(0);
  expect(screen.queryByText("No chats yet.")).not.toBeInTheDocument();
});

test("a failure says so and can be tried again", async () => {
  routeFetch(fetchMock, { "GET /sessions": () => reply(500, "boom") });
  renderInSidebar(<ChatList />);
  expect(await screen.findByText("Could not load your chats.")).toBeInTheDocument();

  routeFetch(fetchMock, { "GET /sessions": () => reply(200, [chat("a", "back again", 1)]) });
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));

  expect(await screen.findByRole("link", { name: /back again/ })).toBeInTheDocument();
  expect(callsTo(fetchMock, "GET /sessions")).toHaveLength(2); // the failed load and the retry
});
