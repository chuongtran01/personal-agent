import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { ApiError } from "@/api/http";
import type { SendResult } from "@/lib/chat-store";

import { Composer } from "./composer";

const OK: SendResult = { ok: true };
const refused = (code: string): SendResult => ({ ok: false, error: new ApiError(409, code, "no") });
const field = () => screen.getByLabelText("Message");
const send = () => screen.getByRole("button", { name: "Send" });

describe("sending", () => {
  test("Enter sends the text, trimmed, and clears the box", async () => {
    const onSend = vi.fn().mockResolvedValue(OK);
    render(<Composer onSend={onSend} />);

    await userEvent.type(field(), "  hello there  {Enter}");

    expect(onSend).toHaveBeenCalledExactlyOnceWith("hello there");
    await waitFor(() => expect(field()).toHaveValue(""));
  });

  test("the Send button sends too", async () => {
    const onSend = vi.fn().mockResolvedValue(OK);
    render(<Composer onSend={onSend} />);

    await userEvent.type(field(), "hi");
    await userEvent.click(send());

    expect(onSend).toHaveBeenCalledExactlyOnceWith("hi");
  });

  test("Shift+Enter makes a new line and sends nothing", async () => {
    const onSend = vi.fn().mockResolvedValue(OK);
    render(<Composer onSend={onSend} />);

    await userEvent.type(field(), "line one{Shift>}{Enter}{/Shift}line two");

    expect(onSend).not.toHaveBeenCalled();
    expect(field()).toHaveValue("line one\nline two");
  });

  test("Enter while composing characters (an IME) confirms them and does not send", async () => {
    const onSend = vi.fn().mockResolvedValue(OK);
    render(<Composer onSend={onSend} />);
    await userEvent.type(field(), "にほん");

    fireEvent.keyDown(field(), { key: "Enter", isComposing: true });

    expect(onSend).not.toHaveBeenCalled();
  });

  test("an empty or blank message is never sent", async () => {
    const onSend = vi.fn().mockResolvedValue(OK);
    render(<Composer onSend={onSend} />);
    expect(send()).toBeDisabled();

    await userEvent.type(field(), "   {Enter}");

    expect(onSend).not.toHaveBeenCalled();
    expect(send()).toBeDisabled();
  });

  test("while a reply is arriving nothing can be sent", async () => {
    const onSend = vi.fn().mockResolvedValue(OK);
    render(<Composer disabled onSend={onSend} />);

    await userEvent.type(field(), "hi{Enter}");

    expect(send()).toBeDisabled();
    expect(onSend).not.toHaveBeenCalled();
    expect(field()).toHaveValue("hi"); // what was typed is kept
  });

  test("a second send while the first is being taken does nothing", async () => {
    let finish: (value: SendResult) => void = () => {};
    const onSend = vi.fn(() => new Promise<SendResult>((resolve) => (finish = resolve)));
    render(<Composer onSend={onSend} />);
    await userEvent.type(field(), "hi");

    await userEvent.click(send());
    fireEvent.submit(field().closest("form")!);

    expect(onSend).toHaveBeenCalledOnce();
    finish(OK);
    await waitFor(() => expect(field()).toHaveValue(""));
  });
});

describe("a starting text", () => {
  test("is in the box from the start, and can be sent as it is", async () => {
    const onSend = vi.fn().mockResolvedValue(OK);
    render(<Composer initialText="What do you remember about me?" onSend={onSend} />);

    expect(field()).toHaveValue("What do you remember about me?");
    await userEvent.click(send());

    expect(onSend).toHaveBeenCalledExactlyOnceWith("What do you remember about me?");
  });

  test("can be edited before it is sent", async () => {
    const onSend = vi.fn().mockResolvedValue(OK);
    render(<Composer initialText="Hello" onSend={onSend} />);

    await userEvent.type(field(), " there{Enter}");

    expect(onSend).toHaveBeenCalledExactlyOnceWith("Hello there");
  });
});

describe("a message the server will not take", () => {
  test("stays in the box, with the reason", async () => {
    const onSend = vi.fn().mockResolvedValue(refused("turn_in_progress"));
    render(<Composer onSend={onSend} />);

    await userEvent.type(field(), "my question{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("Still answering the last message");
    expect(field()).toHaveValue("my question");
    expect(field()).toHaveFocus();
  });

  test("a full chat offers a new one", async () => {
    render(<Composer onSend={vi.fn().mockResolvedValue(refused("chat_full"))} />);

    await userEvent.type(field(), "more{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("This chat is full");
    expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("href", "/");
  });

  test("the reason goes away with the next try", async () => {
    const onSend = vi
      .fn()
      .mockResolvedValueOnce(refused("message_too_long"))
      .mockResolvedValueOnce(OK);
    render(<Composer onSend={onSend} />);
    await userEvent.type(field(), "x{Enter}");
    await screen.findByRole("alert");

    await userEvent.type(field(), "{Enter}");

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});

describe("the length limit", () => {
  const long = (n: number) => "a".repeat(n);

  test("no counter for a short message", async () => {
    render(<Composer onSend={vi.fn()} />);

    fireEvent.change(field(), { target: { value: long(3499) } });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  test("a counter appears near the limit", () => {
    render(<Composer onSend={vi.fn()} />);

    fireEvent.change(field(), { target: { value: long(3500) } });

    expect(screen.getByRole("status")).toHaveTextContent("3500 / 4000");
    expect(send()).toBeEnabled();
  });

  test("4,000 characters can be sent and 4,001 cannot", () => {
    render(<Composer onSend={vi.fn()} />);

    fireEvent.change(field(), { target: { value: long(4000) } });
    expect(send()).toBeEnabled();

    fireEvent.change(field(), { target: { value: long(4001) } });
    expect(send()).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("4001 / 4000");
    expect(field()).toHaveAttribute("aria-invalid", "true");
  });

  test("Enter does not send a message that is too long", () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} />);
    fireEvent.change(field(), { target: { value: long(4001) } });

    fireEvent.keyDown(field(), { key: "Enter" });

    expect(onSend).not.toHaveBeenCalled();
  });
});
