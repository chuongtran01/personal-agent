import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { request } from "@/api/http";
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
  expect(nav.router.replace).toHaveBeenCalledWith("/login?next=%2Fchats%2Fx%3Fy%3D1");
});

test("once the page is gone, a refused token no longer redirects", async () => {
  setToken("abc", 60);
  const { unmount } = protectedPage();
  unmount();
  fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));

  await failure(request("/sessions"));

  expect(nav.router.replace).not.toHaveBeenCalled();
});
