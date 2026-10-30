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

// While the menu is open the rest of the page is hidden from assistive technology, the button too
const account = () => screen.getByRole("button", { name: "Owner", hidden: true });

test("who is signed in is a button at the bottom of the sidebar, with a menu that starts closed", () => {
  render(<AppShell>x</AppShell>);

  expect(account()).toHaveAttribute("aria-haspopup", "menu");
  expect(account()).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Log out" })).not.toBeInTheDocument();
});

test("clicking it opens the menu, and Escape closes it without signing out", async () => {
  setToken("abc", 60);
  render(<AppShell>x</AppShell>);

  await userEvent.click(account());

  const menu = await screen.findByRole("menu");
  expect(account()).toHaveAttribute("aria-expanded", "true");
  expect(within(menu).getByText("Owner")).toBeInTheDocument();
  expect(within(menu).getByRole("menuitem", { name: "Log out" })).toBeInTheDocument();

  await userEvent.keyboard("{Escape}");

  await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  expect(getToken()).toBe("abc");
  expect(nav.router.replace).not.toHaveBeenCalled();
});

test("the menu opens from the keyboard too", async () => {
  render(<AppShell>x</AppShell>);
  account().focus();

  await userEvent.keyboard("{Enter}");

  expect(await screen.findByRole("menuitem", { name: "Log out" })).toBeInTheDocument();
});

test("Log out in the menu forgets the token and every chat held in the browser, then goes to login", async () => {
  setToken("abc", 60);
  await loadChat("c1");
  await refreshChatList();
  expect(getChat("c1").messages).toHaveLength(1);
  render(<AppShell>x</AppShell>);

  await userEvent.click(account());
  await userEvent.click(await screen.findByRole("menuitem", { name: "Log out" }));

  expect(getToken()).toBeNull();
  expect(getChat("c1").messages).toEqual([]);
  expect(getChatList().items).toBeNull();
  expect(nav.router.replace).toHaveBeenCalledWith("/login");
});
