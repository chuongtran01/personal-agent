import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { getToken, setToken } from "@/auth/token";
import { getChat, getChatList, loadChat, refreshChatList, resetStore } from "@/lib/chat-store";
import { fakeFetch, reply, routeFetch } from "@/test/fetch";
import { nav } from "@/test/navigation";
import { setWindowWidth } from "@/test/sidebar";

import { AppShell } from "./app-shell";

vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

const { fetchMock } = fakeFetch();
const chat = { id: "c1", created_at: new Date().toISOString(), message_count: 2, preview: "My first chat" };
const sidebar = (container: HTMLElement) => container.querySelector<HTMLElement>('[data-slot="sidebar"]')!;

beforeEach(() => {
  resetStore();
  nav.router.replace.mockReset();
  nav.pathname = "/";
  setWindowWidth(1024);
  routeFetch(fetchMock, {
    "GET /sessions": () => reply(200, [chat]),
    "GET /sessions/c1/messages": () =>
      reply(200, [{ id: 1, role: "user", text: "private", created_at: chat.created_at, memories: [] }]),
  });
});
afterEach(() => setWindowWidth(1024));

test("it frames the page it is given, beside the sidebar", async () => {
  render(
    <AppShell>
      <p>the page</p>
    </AppShell>,
  );

  expect(screen.getByText("the page")).toBeInTheDocument();
  expect(screen.getByText("Personal Agent")).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "My first chat" })).toHaveAttribute("href", "/chats/c1");
});

test("the sidebar offers a new chat, then the recent chats", async () => {
  render(<AppShell>x</AppShell>);

  const newChat = screen.getByRole("link", { name: "New chat" });
  expect(newChat).toHaveAttribute("href", "/");
  expect(screen.getByText("Recents")).toBeInTheDocument();
  await screen.findByRole("link", { name: "My first chat" });
});

test("marks where the owner is: New chat on the home page, a chat on its own page", async () => {
  const { unmount } = render(<AppShell>x</AppShell>);
  await screen.findByRole("link", { name: "My first chat" });
  expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("link", { name: "My first chat" })).toHaveAttribute("data-active", "false");
  unmount();

  nav.pathname = "/chats/c1";
  render(<AppShell>x</AppShell>);
  expect(await screen.findByRole("link", { name: "My first chat" })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("data-active", "false");
});

test("the sidebar can be folded away and brought back", async () => {
  const { container } = render(<AppShell>x</AppShell>);
  expect(sidebar(container)).toHaveAttribute("data-state", "expanded");

  await userEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }));
  expect(sidebar(container)).toHaveAttribute("data-state", "collapsed");

  await userEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }));
  expect(sidebar(container)).toHaveAttribute("data-state", "expanded");
});

test("on a narrow screen the sidebar is a drawer behind the same button", async () => {
  setWindowWidth(500);
  render(<AppShell>x</AppShell>);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByText("Recents")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }));

  const drawer = await screen.findByRole("dialog");
  expect(within(drawer).getByText("Recents")).toBeInTheDocument();
  expect(await within(drawer).findByRole("link", { name: "My first chat" })).toBeInTheDocument();
});

test("following a chat in the drawer closes it", async () => {
  setWindowWidth(500);
  render(<AppShell>x</AppShell>);
  await userEvent.click(screen.getByRole("button", { name: "Toggle Sidebar" }));
  const drawer = await screen.findByRole("dialog");

  await userEvent.click(await within(drawer).findByRole("link", { name: "My first chat" }));

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
