import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { ChatMessage } from "@/api/types";
import { setToken } from "@/auth/token";
import { resetStore } from "@/lib/chat-store";
import { callsTo, fakeFetch, reply, routeFetch } from "@/test/fetch";
import { nav } from "@/test/navigation";
import { DONE, MEMORY, liveReply } from "@/test/stream";

import { ChatView } from "./chat-view";

vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

const { fetchMock } = fakeFetch();

const stored = (id: number, role: "user" | "assistant", text: string, memories: ChatMessage["memories"] = []): ChatMessage => ({
  id,
  role,
  text,
  created_at: "2026-10-30T09:00:00Z",
  memories,
});
const message = () => screen.getByLabelText("Message");
const sendButton = () => screen.getByRole("button", { name: "Send" });

beforeEach(() => {
  resetStore();
  nav.router.replace.mockReset();
  setToken("abc", 600);
});

describe("opening a chat", () => {
  test("shows a skeleton, then the history with each reply's memories", async () => {
    let release: (value: Response) => void = () => {};
    routeFetch(fetchMock, {
      "GET /sessions/c1/messages": () => new Promise<Response>((resolve) => (release = resolve)),
      "GET /sessions": () => reply(200, []),
    });
    const { container } = render(<ChatView chatId="c1" />);

    expect(container.querySelector("[data-slot=skeleton]")).not.toBeNull();
    expect(screen.queryByRole("log")).not.toBeInTheDocument();
    await userEvent.type(message(), "x");
    expect(sendButton()).toBeDisabled(); // nothing to say to a chat that has not loaded

    release(
      reply(200, [
        stored(1, "user", "Where do I live?"),
        stored(2, "assistant", "In Porto.", [
          { id: 1, kind: "fact", content: "The owner lives in Porto.", past: false, status: "active" },
        ]),
      ]),
    );

    expect(await screen.findByText("In Porto.")).toBeInTheDocument();
    expect(screen.getByText("Where do I live?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Recalled 1 memory/ })).toBeInTheDocument();
    expect(sendButton()).toBeEnabled();
  });

  test("a chat that does not exist sends the owner home", async () => {
    routeFetch(fetchMock, {
      "GET /sessions/gone/messages": () =>
        reply(404, { detail: { code: "session_not_found", message: "no such chat" } }),
    });

    render(<ChatView chatId="gone" />);

    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/"));
  });

  test("a failure to load says so and can be tried again", async () => {
    routeFetch(fetchMock, { "GET /sessions/c1/messages": () => reply(500, "boom") });
    render(<ChatView chatId="c1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load this chat");
    expect(nav.router.replace).not.toHaveBeenCalled();

    routeFetch(fetchMock, { "GET /sessions/c1/messages": () => reply(200, [stored(1, "user", "back")]) });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("back")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("sending a message", () => {
  async function openChat() {
    const live = liveReply();
    routeFetch(fetchMock, {
      "GET /sessions/c1/messages": () => reply(200, []),
      "GET /sessions": () => reply(200, []),
      "POST /sessions/c1/messages": () => live.response(),
    });
    render(<ChatView chatId="c1" />);
    await screen.findByLabelText("Message");
    await waitFor(() => expect(callsTo(fetchMock, "GET /sessions/c1/messages")).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("log")).toBeInTheDocument());
    return live;
  }

  test("the question appears at once, then the memories, then the reply as it streams", async () => {
    const live = await openChat();

    await userEvent.type(message(), "Where do I live?{Enter}");

    expect(await screen.findByText("Where do I live?")).toBeInTheDocument();
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
    // One reply at a time: the box stays free for the next message, but it cannot be sent yet
    await userEvent.type(message(), "next question");
    expect(sendButton()).toBeDisabled();

    act(() => live.push("memory", MEMORY));
    expect(await screen.findByRole("button", { name: /Recalled 1 memory/ })).toBeInTheDocument();
    expect(screen.getByText("Thinking…")).toBeInTheDocument(); // the list arrives before the first word

    act(() => {
      live.push("text", { text: "You live in " });
      live.push("text", { text: "**Porto**." });
    });
    expect(await screen.findByText("Porto")).toBeInTheDocument();
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument();
    expect(sendButton()).toBeDisabled();
    expect(message()).toHaveValue("next question"); // what was typed meanwhile is kept
  });

  test("when the reply is done the owner can write again, with the memories still under it", async () => {
    const live = await openChat();
    routeFetch(fetchMock, {
      "GET /sessions/c1/messages": () =>
        reply(200, [
          stored(7, "user", "Where do I live?"),
          stored(41, "assistant", "In Porto.", [
            { id: 1, kind: "fact", content: "The owner lives in Porto.", past: false, status: "active" },
          ]),
        ]),
      "GET /sessions": () => reply(200, []),
      "POST /sessions/c1/messages": () => live.response(),
    });
    await userEvent.type(message(), "Where do I live?{Enter}");

    act(() => {
      live.push("memory", MEMORY);
      live.push("text", { text: "In Porto." });
      live.push("done", DONE);
      live.end();
    });

    await userEvent.type(message(), "and another");
    await waitFor(() => expect(sendButton()).toBeEnabled());
    expect(screen.getByText("In Porto.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Recalled 1 memory/ })).toBeInTheDocument();
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument();
  });

  test("an error event shows the server's message and leaves the composer free", async () => {
    const live = await openChat();
    await userEvent.type(message(), "q{Enter}");

    act(() => {
      live.push("text", { text: "half" });
      live.push("error", { code: "model_error", message: "the model could not answer; please try again" });
      live.end();
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("the model could not answer");
    expect(screen.queryByText("half")).not.toBeInTheDocument();
    await userEvent.type(message(), "try again");
    await waitFor(() => expect(sendButton()).toBeEnabled());
  });

  test("a refused message stays in the box and the chat is not changed", async () => {
    routeFetch(fetchMock, {
      "GET /sessions/c1/messages": () => reply(200, [stored(1, "user", "hi"), stored(2, "assistant", "hello")]),
      "GET /sessions": () => reply(200, []),
      "POST /sessions/c1/messages": () =>
        reply(409, { detail: { code: "chat_full", message: "this chat is full; start a new one" } }),
    });
    render(<ChatView chatId="c1" />);
    await screen.findByText("hello");

    await userEvent.type(message(), "one more{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("This chat is full");
    expect(message()).toHaveValue("one more");
    expect(screen.queryByText("one more", { selector: "div" })).not.toBeInTheDocument();
    expect(callsTo(fetchMock, "POST /sessions/c1/messages")).toHaveLength(1);
  });
});

describe("following the reply", () => {
  // jsdom has no layout, so the scroll position is faked on the scrolling element
  function scroller(container: HTMLElement) {
    const element = container.querySelector<HTMLElement>(".overflow-y-auto")!;
    let top = 0;
    Object.defineProperty(element, "scrollHeight", { configurable: true, get: () => 1000 });
    Object.defineProperty(element, "clientHeight", { configurable: true, get: () => 400 });
    Object.defineProperty(element, "scrollTop", { configurable: true, get: () => top, set: (v) => (top = v) });
    return { element, top: () => top, scrollTo: (v: number) => { top = v; element.dispatchEvent(new Event("scroll")); } };
  }

  async function streamingChat() {
    const live = liveReply();
    routeFetch(fetchMock, {
      "GET /sessions/c1/messages": () => reply(200, [stored(1, "user", "q0"), stored(2, "assistant", "a0")]),
      "GET /sessions": () => reply(200, []),
      "POST /sessions/c1/messages": () => live.response(),
    });
    const view = render(<ChatView chatId="c1" />);
    await screen.findByText("a0");
    return { live, ...view };
  }

  test("stays at the bottom while the owner is there", async () => {
    const { live, container } = await streamingChat();
    const s = scroller(container);
    s.scrollTo(590); // within reach of the bottom
    await userEvent.type(message(), "q{Enter}");

    act(() => live.push("text", { text: "more words" }));

    await screen.findByText("more words");
    expect(s.top()).toBe(1000);
  });

  test("does not pull the owner back down after they scrolled up to read", async () => {
    const { live, container } = await streamingChat();
    const s = scroller(container);
    await userEvent.type(message(), "q{Enter}");
    s.scrollTo(100); // reading something above

    act(() => live.push("text", { text: "more words" }));

    await screen.findByText("more words");
    expect(s.top()).toBe(100);
  });
});
