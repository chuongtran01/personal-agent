import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import type { ChatMessage } from "@/api/types";
import type { ActiveTurn } from "@/lib/chat-state";

import { MessageList } from "./message-list";

const message = (id: number, role: "user" | "assistant", text: string, memories: ChatMessage["memories"] = []): ChatMessage => ({
  id,
  role,
  text,
  created_at: "2026-10-30T09:00:00Z",
  memories,
});
const streaming = (overrides: Partial<ActiveTurn> = {}): ActiveTurn => ({
  memories: null,
  text: "",
  status: "streaming",
  error: null,
  ...overrides,
});
const view = (messages: ChatMessage[], turn: ActiveTurn | null = null, onReload = vi.fn()) =>
  render(<MessageList messages={messages} turn={turn} onReload={onReload} />);

describe("the conversation", () => {
  test("is a log region a screen reader follows", () => {
    view([]);

    expect(screen.getByRole("log", { name: "Conversation" })).toHaveAttribute("aria-live", "polite");
  });

  test("shows the owner's messages and the replies in order", () => {
    view([message(1, "user", "Where do I live?"), message(2, "assistant", "**Porto**.")]);

    const log = screen.getByRole("log");
    expect(log.textContent).toMatch(/Where do I live\?.*Porto/);
    expect(log.querySelector("strong")).toHaveTextContent("Porto");
  });

  test("keeps the line breaks the owner typed", () => {
    view([message(1, "user", "line one\nline two")]);

    expect(screen.getByText(/line one/)).toHaveClass("whitespace-pre-wrap");
  });

  test("shows what was recalled under the reply that used it, and only there", () => {
    view([
      message(1, "user", "q"),
      message(2, "assistant", "a", [
        { id: 1, kind: "fact", content: "x", past: false, status: "active" },
      ]),
      message(3, "user", "q2"),
      message(4, "assistant", "b"),
    ]);

    expect(screen.getAllByRole("button", { name: /Recalled 1 memory/ })).toHaveLength(1);
    expect(screen.getAllByText("No memory matched")).toHaveLength(1);
  });
});

describe("a reply that is coming", () => {
  test("says it is thinking until the first word", () => {
    view([message(1, "user", "q")], streaming());

    expect(screen.getByText("Thinking…")).toBeInTheDocument();
  });

  test("shows the recalled memories before any text", () => {
    view(
      [message(1, "user", "q")],
      streaming({ memories: [{ id: 1, kind: "fact", content: "x", past: false, status: "active" }] }),
    );

    expect(screen.getByRole("button", { name: /Recalled 1 memory/ })).toBeInTheDocument();
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
  });

  test("shows the words as they arrive, as Markdown", () => {
    view([message(1, "user", "q")], streaming({ text: "You live in **Porto**" }));

    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument();
    expect(screen.getByText("Porto").tagName).toBe("STRONG");
  });
});

describe("a turn that failed", () => {
  const failed = (code: string, message: string): ActiveTurn =>
    streaming({ status: "failed", error: { code, message } });

  test("says what went wrong and that the message was saved but not answered", () => {
    view([message(1, "user", "q")], failed("model_error", "the model could not answer; please try again"));

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("the model could not answer; please try again");
    expect(alert).toHaveTextContent("saved but not answered");
    expect(screen.queryByRole("button", { name: "Reload" })).not.toBeInTheDocument();
  });

  test("a lost connection offers a reload, to see whether the reply was saved", async () => {
    const onReload = vi.fn();
    view([message(1, "user", "q")], failed("connection_lost", "The connection was lost."), onReload);

    await userEvent.click(screen.getByRole("button", { name: "Reload" }));

    expect(onReload).toHaveBeenCalledOnce();
  });
});
