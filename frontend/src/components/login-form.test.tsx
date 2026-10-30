import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { getToken, setToken } from "@/auth/token";
import { fakeFetch, reply } from "@/test/fetch";
import { nav } from "@/test/navigation";

import { LoginForm } from "./login-form";

// Hoisted above the imports, so the shared mock is loaded when the module is first needed
vi.mock("next/navigation", async () => (await import("@/test/navigation")).navigationMock());

const { fetchMock } = fakeFetch();

const LOGGED_IN = { access_token: "tok", token_type: "bearer", expires_in: 3600 };
const page = (url: string) => window.history.pushState({}, "", url);
const field = () => screen.getByLabelText("Password");
const submit = () => screen.getByRole("button", { name: /log in|logging in/i });

beforeEach(() => {
  nav.router.replace.mockReset();
  page("/login");
});

async function logIn(password = "correct horse") {
  await userEvent.type(field(), password);
  await userEvent.click(submit());
}

test("it asks for a password, and the button waits until there is one", async () => {
  render(<LoginForm />);

  expect(field()).toHaveAttribute("type", "password");
  expect(field()).toHaveAttribute("autocomplete", "current-password");
  expect(submit()).toBeDisabled();

  await userEvent.type(field(), "x");
  expect(submit()).toBeEnabled();
});

describe("a right password", () => {
  test("keeps the token and goes to the home page", async () => {
    fetchMock.mockResolvedValue(reply(200, LOGGED_IN));
    render(<LoginForm />);

    await logIn();

    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/"));
    expect(getToken()).toBe("tok");
  });

  test("Enter in the field sends it", async () => {
    fetchMock.mockResolvedValue(reply(200, LOGGED_IN));
    render(<LoginForm />);

    await userEvent.type(field(), "correct horse{Enter}");

    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/"));
  });

  test("goes back to the page the owner was headed for", async () => {
    page("/login?next=%2Fchats%2Fabc%3Ftab%3D2");
    fetchMock.mockResolvedValue(reply(200, LOGGED_IN));
    render(<LoginForm />);

    await logIn();

    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/chats/abc?tab=2"));
  });

  test.each(["//evil.example", "https://evil.example", "javascript:alert(1)", "/login"])(
    "does not follow a next of %s",
    async (next) => {
      page(`/login?next=${encodeURIComponent(next)}`);
      fetchMock.mockResolvedValue(reply(200, LOGGED_IN));
      render(<LoginForm />);

      await logIn();

      await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/"));
      expect(nav.router.replace).toHaveBeenCalledOnce();
    },
  );

  test("sends the password once, as the request body only", async () => {
    fetchMock.mockResolvedValue(reply(200, LOGGED_IN));
    render(<LoginForm />);

    await logIn("s3cret");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/auth/login");
    expect(JSON.parse(String(init?.body))).toEqual({ password: "s3cret" });
    expect(window.location.href).not.toContain("s3cret");
  });
});

describe("a login that fails", () => {
  test("a wrong password says so, keeps what was typed, and does not navigate", async () => {
    fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));
    render(<LoginForm />);

    await logIn("nope");

    expect(await screen.findByRole("alert")).toHaveTextContent("That password is not right.");
    expect(field()).toHaveValue("nope");
    expect(field()).toHaveFocus();
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(nav.router.replace).not.toHaveBeenCalled();
    expect(getToken()).toBeNull();
  });

  test("a server with login not set up says that, without naming settings", async () => {
    fetchMock.mockResolvedValue(reply(503, { detail: "owner login is not configured" }));
    render(<LoginForm />);

    await logIn();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Login is not available: the server is not set up.");
    expect(alert.textContent).not.toMatch(/JWT|HASH|secret/i);
  });

  test("a server that cannot be reached says so", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<LoginForm />);

    await logIn();

    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server.");
  });

  test("anything else gets a plain message, not the server's text", async () => {
    fetchMock.mockResolvedValue(reply(500, "Traceback: secret internals"));
    render(<LoginForm />);

    await logIn();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert.textContent).not.toContain("Traceback");
  });

  test("the form works again afterwards, and the old error goes away", async () => {
    fetchMock
      .mockResolvedValueOnce(reply(401, { detail: "invalid credentials" }))
      .mockResolvedValueOnce(reply(200, LOGGED_IN));
    render(<LoginForm />);
    await logIn("nope");
    await screen.findByRole("alert");

    await userEvent.clear(field());
    await userEvent.type(field(), "right");
    await userEvent.click(submit());

    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

test("while it waits for the server the button is locked and a second send does nothing", async () => {
  let release: (value: Response) => void = () => {};
  fetchMock.mockImplementation(() => new Promise<Response>((resolve) => (release = resolve)));
  render(<LoginForm />);

  await userEvent.type(field(), "pw");
  await userEvent.click(submit());
  await userEvent.type(field(), "{Enter}");
  // The button is disabled, but the form itself must refuse too (a script, an autofill, a retry)
  fireEvent.submit(field().closest("form")!);

  expect(submit()).toBeDisabled();
  expect(submit()).toHaveTextContent("Logging in…");
  expect(fetchMock).toHaveBeenCalledOnce();

  release(reply(200, LOGGED_IN));
  await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/"));
});

describe("when the owner is sent here because the server refused their token", () => {
  test("says the session ended, and still takes them back where they were afterwards", async () => {
    page("/login?next=%2Fchats%2Fabc&reason=expired");
    fetchMock.mockResolvedValue(reply(200, LOGGED_IN));
    render(<LoginForm />);

    expect(screen.getByRole("alert")).toHaveTextContent("Your session has ended. Please log in again.");
    await logIn();

    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/chats/abc"));
  });

  test("says nothing of the sort on an ordinary visit", () => {
    page("/login");
    render(<LoginForm />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test.each(["?reason=whatever", "?reason=EXPIRED", "?reason="])("ignores %s", (search) => {
    page(`/login${search}`);
    render(<LoginForm />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("gives way to the error when the password is then wrong", async () => {
    page("/login?reason=expired");
    fetchMock.mockResolvedValue(reply(401, { detail: "invalid credentials" }));
    render(<LoginForm />);

    await logIn("nope");

    const alerts = await screen.findAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent("That password is not right.");
  });
});

test("someone already signed in is sent on without the form doing anything", async () => {
  setToken("abc", 60);
  page("/login?next=%2Fmemories");

  render(<LoginForm />);

  await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/memories"));
  expect(fetchMock).not.toHaveBeenCalled();
});
