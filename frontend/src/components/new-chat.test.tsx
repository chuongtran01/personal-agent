import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { setToken } from "@/auth/token";
import { getChat, resetStore } from "@/lib/chat-store";
import { callsTo, fakeFetch, reply, routeFetch } from "@/test/fetch";
import { nav } from "@/test/navigation";
import { liveReply } from "@/test/stream";

import { NewChat } from "./new-chat";

vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

const { fetchMock } = fakeFetch();
const chat = (id: string) => ({ id, created_at: "2026-10-30T09:00:00Z", message_count: 0, preview: null });
const message = () => screen.getByLabelText("Message");

beforeEach(() => {
  resetStore();
  nav.router.push.mockReset();
  setToken("abc", 600);
});

test("nothing is created until the first message is sent", () => {
  routeFetch(fetchMock, {});

  render(<NewChat />);

  expect(screen.getByRole("heading")).toHaveTextContent("What's on your mind?");
  expect(fetchMock).not.toHaveBeenCalled();
});

test("the first message creates the chat, sends it, and moves to the chat while the reply streams", async () => {
  const live = liveReply();
  routeFetch(fetchMock, {
    "POST /sessions": () => reply(201, chat("new1")),
    "POST /sessions/new1/messages": () => live.response(),
    "GET /sessions/new1/messages": () => reply(200, []),
    "GET /sessions": () => reply(200, []),
  });
  render(<NewChat />);

  await userEvent.type(message(), "Hello{Enter}");

  await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/chats/new1"));
  expect(getChat("new1").messages.map((m) => m.text)).toEqual(["Hello"]); // already waiting there
  live.end();
});

test("a first message the server will not take stays here, with the reason, and goes nowhere", async () => {
  routeFetch(fetchMock, {
    "POST /sessions": () => reply(201, chat("new1")),
    "POST /sessions/new1/messages": () => reply(503, { detail: "the agent is not configured" }),
  });
  render(<NewChat />);

  await userEvent.type(message(), "Hello{Enter}");

  expect(await screen.findByRole("alert")).toHaveTextContent("The assistant is not available");
  expect(message()).toHaveValue("Hello");
  expect(nav.router.push).not.toHaveBeenCalled();
});

test("a chat that cannot be created is reported, and nothing is sent", async () => {
  routeFetch(fetchMock, { "POST /sessions": () => reply(500, "boom") });
  render(<NewChat />);

  await userEvent.type(message(), "Hello{Enter}");

  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(message()).toHaveValue("Hello");
  expect(callsTo(fetchMock, "POST /sessions/new1/messages")).toHaveLength(0);
});

test("offers a few things to ask, which only fill the box", async () => {
  routeFetch(fetchMock, {});
  render(<NewChat />);
  const suggestions = screen.getByRole("list", { name: "Suggestions" });

  await userEvent.click(within(suggestions).getByRole("button", { name: "What do you remember about me?" }));

  expect(message()).toHaveValue("What do you remember about me?");
  expect(message()).toHaveFocus();
  expect(fetchMock).not.toHaveBeenCalled(); // nothing is sent until the owner says so
  expect(within(suggestions).getAllByRole("button")).toHaveLength(3);
});

test("choosing another suggestion replaces the first, and the owner can still edit it", async () => {
  const live = liveReply();
  routeFetch(fetchMock, {
    "POST /sessions": () => reply(201, chat("new1")),
    "POST /sessions/new1/messages": () => live.response(),
    "GET /sessions/new1/messages": () => reply(200, []),
    "GET /sessions": () => reply(200, []),
  });
  render(<NewChat />);
  const suggestions = screen.getByRole("list", { name: "Suggestions" });
  await userEvent.click(within(suggestions).getByRole("button", { name: "What do you remember about me?" }));

  await userEvent.click(within(suggestions).getByRole("button", { name: "What do I have coming up?" }));
  expect(message()).toHaveValue("What do I have coming up?");
  await userEvent.type(message(), " Next week.{Enter}");

  await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/chats/new1"));
  const sent = callsTo(fetchMock, "POST /sessions/new1/messages")[0][1]?.body;
  expect(JSON.parse(String(sent))).toEqual({ text: "What do I have coming up? Next week." });
  live.end();
});
