import { act, render, screen } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { request } from "@/api/http";
import { getChat, loadChat, resetStore } from "@/lib/chat-store";
import { nav } from "@/test/navigation";
import { failure, fakeFetch, reply } from "@/test/fetch";

import { RequireAuth } from "./require-auth";
import { getToken, setToken } from "./token";

// Hoisted above the imports, so the shared mock is loaded when the module is first needed
vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

const { fetchMock } = fakeFetch();

const page = (url: string) => window.history.pushState({}, "", url);
const protectedPage = () =>
  render(
    <RequireAuth>
      <p>secret page</p>
    </RequireAuth>,
  );

beforeEach(() => {
  resetStore();
  nav.router.replace.mockReset();
  nav.pathname = "/";
  page("/");
});

test("a visitor without a token sees nothing and is sent to login, to return here", () => {
  page("/memories");

  protectedPage();

  expect(screen.queryByText("secret page")).not.toBeInTheDocument();
  expect(nav.router.replace).toHaveBeenCalledWith("/login?next=%2Fmemories");
});

test("a visitor who never signed in is not told a session ended", () => {
  page("/memories");

  protectedPage();

  expect(nav.router.replace).toHaveBeenCalledWith("/login?next=%2Fmemories");
  expect(String(nav.router.replace.mock.calls[0][0])).not.toContain("reason");
});

test("from the home page the login address carries no next", () => {
  protectedPage();

  expect(nav.router.replace).toHaveBeenCalledWith("/login");
});

test("the place to return to includes the query", () => {
  page("/chats/abc?tab=2");

  protectedPage();

  expect(nav.router.replace).toHaveBeenCalledWith("/login?next=%2Fchats%2Fabc%3Ftab%3D2");
});

test("an expired token is the same as none", () => {
  setToken("old", -1);
  page("/memories");

  protectedPage();

  expect(screen.queryByText("secret page")).not.toBeInTheDocument();
  expect(nav.router.replace).toHaveBeenCalledOnce();
});

test("an owner with a token sees the page and is not redirected", () => {
  setToken("abc", 60);

  protectedPage();

  expect(screen.getByText("secret page")).toBeInTheDocument();
  expect(nav.router.replace).not.toHaveBeenCalled();
});

test("a token refused later sends the owner to login from wherever they are", async () => {
  setToken("abc", 60);
  protectedPage();
  page("/chats/x?y=1");
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  await failure(request("/sessions"));

  expect(getToken()).toBeNull();
  expect(nav.router.replace).toHaveBeenCalledWith("/login?next=%2Fchats%2Fx%3Fy%3D1&reason=expired");
});

test("once the page is gone, a refused token no longer redirects", async () => {
  setToken("abc", 60);
  const { unmount } = protectedPage();
  unmount();
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  await failure(request("/sessions"));

  expect(nav.router.replace).not.toHaveBeenCalled();
});

test("a token refused later also forgets the chats held in the browser", async () => {
  setToken("abc", 60);
  fetchMock.mockResolvedValueOnce(
    reply(200, [{ id: 1, role: "user", text: "private", created_at: "2026-10-30T09:00:00Z", memories: [] }]),
  );
  await loadChat("c1");
  expect(getChat("c1").messages).toHaveLength(1);
  protectedPage();
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  await failure(request("/sessions"));

  expect(getChat("c1").messages).toEqual([]);
});

// A reload is not a first render: the server sent HTML, and React hydrates it. During hydration the
// guard has no browser to ask yet, and it must not take that for "signed out".
describe("a reload, which hydrates what the server sent", () => {
  const tree = (
    <RequireAuth>
      <p>secret page</p>
    </RequireAuth>
  );

  async function reload() {
    const container = document.createElement("div");
    document.body.appendChild(container);
    container.innerHTML = renderToString(tree); // what the server sends
    expect(container).toBeEmptyDOMElement(); // nothing of the page: it cannot know the token
    await act(async () => {
      hydrateRoot(container, tree);
    });
    return container;
  }

  test("an owner with a token sees the page and is not sent to login on the way", async () => {
    setToken("abc", 60);
    page("/memories");

    const container = await reload();

    expect(nav.router.replace).not.toHaveBeenCalled();
    expect(container).toHaveTextContent("secret page");
  });

  test("a visitor with no token is sent to login, once, to come back here", async () => {
    page("/memories");

    const container = await reload();

    expect(container).toBeEmptyDOMElement();
    expect(nav.router.replace).toHaveBeenCalledExactlyOnceWith("/login?next=%2Fmemories");
  });

  test("a token refused after a reload still sends the owner to login", async () => {
    setToken("abc", 60);
    await reload();
    page("/chats/x");
    fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

    await failure(request("/sessions"));

    expect(nav.router.replace).toHaveBeenCalledWith("/login?next=%2Fchats%2Fx&reason=expired");
  });
});
