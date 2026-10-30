import { render, screen, waitFor } from "@testing-library/react";
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
