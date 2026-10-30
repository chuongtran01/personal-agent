import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { getToken, setToken } from "@/auth/token";
import { nav } from "@/test/navigation";

import { AppShell } from "./app-shell";

// Hoisted above the imports, so the shared mock is loaded when the module is first needed
vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

beforeEach(() => {
  nav.router.replace.mockReset();
  sessionStorage.clear();
});

test("it frames the page it is given", () => {
  render(
    <AppShell>
      <p>the page</p>
    </AppShell>,
  );

  expect(screen.getByText("the page")).toBeInTheDocument();
});

test("logging out forgets the token and goes to login", async () => {
  setToken("abc", 60);
  render(<AppShell>x</AppShell>);

  await userEvent.click(screen.getByRole("button", { name: "Log out" }));

  expect(getToken()).toBeNull();
  expect(nav.router.replace).toHaveBeenCalledWith("/login");
});
