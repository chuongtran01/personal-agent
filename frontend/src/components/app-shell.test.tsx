import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { getToken, setToken } from "@/auth/token";
import { getChat, getChatList, loadChat, refreshChatList, resetStore } from "@/lib/chat-store";
import { fakeFetch, reply, routeFetch } from "@/test/fetch";
import { nav } from "@/test/navigation";

import { AppShell } from "./app-shell";

vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

const { fetchMock } = fakeFetch();
const chat = { id: "c1", created_at: new Date().toISOString(), message_count: 2, preview: "My first chat" };

beforeEach(() => {
  resetStore();
  nav.router.replace.mockReset();
  nav.pathname = "/";
  routeFetch(fetchMock, {
    "GET /sessions": () => reply(200, [chat]),
    "GET /sessions/c1/messages": () => reply(200, [{ id: 1, role: "user", text: "private", created_at: chat.created_at, memories: [] }]),
  });
});

test("it frames the page it is given, with the chat list beside it", async () => {
  render(
    <AppShell>
      <p>the page</p>
    </AppShell>,
  );

  expect(screen.getByText("the page")).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: /My first chat/ })).toHaveAttribute("href", "/chats/c1");
});

test("on a narrow screen the chats are in a drawer behind a menu button", async () => {
  render(<AppShell>x</AppShell>);
  await screen.findByRole("link", { name: /My first chat/ });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getAllByRole("navigation", { name: "Chats" })).toHaveLength(1); // the side list

  await userEvent.click(screen.getByRole("button", { name: "Open chats" }));

  const drawer = await screen.findByRole("dialog", { name: "Chats" });
  expect(within(drawer).getByRole("navigation", { name: "Chats" })).toBeInTheDocument();
  expect(await within(drawer).findByRole("link", { name: /My first chat/ })).toBeInTheDocument();
});

test("following a chat in the drawer closes it", async () => {
  render(<AppShell>x</AppShell>);
  await userEvent.click(screen.getByRole("button", { name: "Open chats" }));
  const drawer = await screen.findByRole("dialog", { name: "Chats" });

  await userEvent.click(await within(drawer).findByRole("link", { name: /My first chat/ }));

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("logging out forgets the token and every chat held in the browser, then goes to login", async () => {
  setToken("abc", 60);
  await loadChat("c1");
  await refreshChatList();
  expect(getChat("c1").messages).toHaveLength(1);
  render(<AppShell>x</AppShell>);

  await userEvent.click(screen.getByRole("button", { name: "Log out" }));

  expect(getToken()).toBeNull();
  expect(getChat("c1").messages).toEqual([]);
  expect(getChatList().items).toBeNull();
  expect(nav.router.replace).toHaveBeenCalledWith("/login");
});
