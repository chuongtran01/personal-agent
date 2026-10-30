import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, test } from "vitest";

import type { Memory } from "@/api/types";
import { setToken } from "@/auth/token";
import { fakeFetch, reply } from "@/test/fetch";

import { MemoryList } from "./memory-list";

const { fetchMock } = fakeFetch();

const memory = (id: number, overrides: Partial<Memory> = {}): Memory => ({
  id,
  kind: "fact",
  content: `Memory number ${id}.`,
  status: "active",
  source_kind: "owner",
  superseded_by: null,
  valid_from: null,
  valid_until: null,
  created_at: "2026-08-01T12:00:00Z",
  last_used_at: null,
  ...overrides,
});
const many = (count: number, from = 1, overrides: Partial<Memory> = {}) =>
  Array.from({ length: count }, (_, i) => memory(from + i, overrides));

/** Answer /memories by what was asked: `"status:offset"` -> the page. Anything else is an empty page. */
function serve(pages: Record<string, Memory[] | Response> = {}) {
  fetchMock.mockImplementation(async (input) => {
    const url = new URL(String(input), "http://test");
    const key = `${url.searchParams.get("status") ?? "all"}:${url.searchParams.get("offset")}`;
    const page = pages[key];
    return page instanceof Response ? page : reply(200, page ?? []);
  });
}
const asked = () =>
  fetchMock.mock.calls.map(([input]) => {
    const url = new URL(String(input), "http://test");
    return `${url.searchParams.get("status") ?? "all"}:${url.searchParams.get("offset")}:${url.searchParams.get("limit")}`;
  });
const tab = (name: string) => screen.getByRole("tab", { name });

beforeEach(() => setToken("abc", 600));

describe("opening the list", () => {
  test("shows the active memories first, and only asks for those", async () => {
    serve({ "active:0": [memory(1), memory(2)] });
    render(<MemoryList />);

    expect(await screen.findByText("Memory number 1.")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Memories" })).toBeInTheDocument();
    expect(tab("Active")).toHaveAttribute("aria-selected", "true");
    expect(asked()).toEqual(["active:0:100"]);
    expect(screen.getByRole("status")).toHaveTextContent("2 loaded");
  });

  test("asks for the first page once even when React runs effects twice, as it does in development", async () => {
    serve({ "active:0": [memory(1)] });
    render(
      <StrictMode>
        <MemoryList />
      </StrictMode>,
    );

    await screen.findByText("Memory number 1.");

    expect(asked()).toEqual(["active:0:100"]);
  });

  test("shows placeholders while the first page loads", () => {
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));

    const { container } = render(<MemoryList />);

    expect(container.querySelectorAll("[data-slot=skeleton]").length).toBeGreaterThan(0);
    expect(screen.queryByText("No active memories yet.")).not.toBeInTheDocument();
  });

  test("says so when there is nothing yet", async () => {
    serve();
    render(<MemoryList />);

    expect(await screen.findByText("No active memories yet.")).toBeInTheDocument();
  });

  test("is a view only: nothing to edit, delete or forget", async () => {
    serve({ "active:0": [memory(1)] });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    expect(screen.queryByRole("button", { name: /edit|delete|forget|remove/i })).not.toBeInTheDocument();
  });
});

describe("a memory's row", () => {
  test("shows its kind, its text, when it was made, and that it was never shown", async () => {
    serve({ "active:0": [memory(1, { kind: "preference", content: "Still water only." })] });
    render(<MemoryList />);

    const row = (await screen.findByText("Still water only.")).closest("li")!;

    expect(within(row).getByText("preference")).toBeInTheDocument();
    expect(row).toHaveTextContent("Created Aug 1, 2026");
    expect(row).toHaveTextContent("Never shown");
    expect(within(row).queryByText("external")).not.toBeInTheDocument();
  });

  test("says when it was last shown to the model, in words", async () => {
    const fiveMinutesAgo = new Date(Date.now() - 5 * 60_000).toISOString();
    serve({ "active:0": [memory(1, { last_used_at: fiveMinutesAgo })] });
    render(<MemoryList />);

    expect(await screen.findByText(/Last shown 5 minutes ago/)).toBeInTheDocument();
  });

  test("says when it holds, if it does not hold always", async () => {
    serve({ "active:0": [memory(1, { valid_from: "2027-01-04T12:00:00Z" }), memory(2)] });
    render(<MemoryList />);

    const rows = await screen.findAllByRole("listitem");

    expect(rows[0]).toHaveTextContent("Holds from Jan 4, 2027");
    expect(rows[1]).not.toHaveTextContent("Holds");
  });

  test("marks one that did not come from the owner", async () => {
    serve({ "active:0": [memory(1, { source_kind: "external", content: "From an email." })] });
    render(<MemoryList />);

    const row = (await screen.findByText("From an email.")).closest("li")!;

    expect(within(row).getByText("external")).toBeInTheDocument();
  });

  test("a replaced memory says it was, and by which", async () => {
    serve({
      "superseded:0": [memory(3, { status: "superseded", superseded_by: 9, content: "Lived in Lisbon." })],
    });
    render(<MemoryList />);
    await userEvent.click(tab("Replaced"));

    const row = (await screen.findByText("Lived in Lisbon.")).closest("li")!;

    expect(within(row).getByText("replaced")).toBeInTheDocument();
    expect(row).toHaveTextContent("Replaced by memory 9");
  });

  test("a deleted memory is shown, softened, under its own tab", async () => {
    serve({ "deleted:0": [memory(4, { status: "deleted", content: "Something forgotten." })] });
    render(<MemoryList />);
    await userEvent.click(tab("Deleted"));

    const text = await screen.findByText("Something forgotten.");

    expect(within(text.closest("li")!).getByText("deleted")).toBeInTheDocument();
    expect(text).toHaveClass("text-muted-foreground");
  });
});

describe("the tabs", () => {
  test("each asks for its own status, and All for every status", async () => {
    serve({ "active:0": [memory(1)], "superseded:0": [], "deleted:0": [], "all:0": [memory(1)] });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await userEvent.click(tab("Replaced"));
    await userEvent.click(tab("Deleted"));
    await userEvent.click(tab("All"));

    await waitFor(() => expect(asked()).toHaveLength(4));
    expect(asked()).toEqual(["active:0:100", "superseded:0:100", "deleted:0:100", "all:0:100"]);
  });

  test("going back to a tab shows what it had without asking again", async () => {
    serve({ "active:0": [memory(1)], "superseded:0": [] });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await userEvent.click(tab("Replaced"));
    await screen.findByText("Nothing has been replaced yet.");

    await userEvent.click(tab("Active"));

    expect(screen.getByText("Memory number 1.")).toBeVisible();
    expect(asked()).toHaveLength(2);
  });

  test("a tab that has not been opened has not been asked for", async () => {
    serve({ "active:0": [memory(1)] });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    expect(asked().filter((a) => !a.startsWith("active"))).toEqual([]);
  });
});

describe("load more", () => {
  test("is offered after a full page, asks for the next 100, and goes when the list runs out", async () => {
    serve({ "active:0": many(100), "active:100": many(50, 101) });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    expect(screen.getByRole("status")).toHaveTextContent("100 loaded so far");

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Memory number 150.")).toBeInTheDocument();
    expect(asked()).toEqual(["active:0:100", "active:100:100"]);
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("150 loaded");
    expect(screen.getByRole("status")).not.toHaveTextContent("so far");
  });

  test("is not offered when the first page is short", async () => {
    serve({ "active:0": many(99) });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  test("is locked while it loads, and a second click does not ask twice", async () => {
    let release: (value: Response) => void = () => {};
    fetchMock.mockImplementation(async (input) => {
      const offset = new URL(String(input), "http://test").searchParams.get("offset");
      return offset === "0" ? reply(200, many(100)) : new Promise<Response>((resolve) => (release = resolve));
    });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    const loading = screen.getByRole("button", { name: "Loading…" });
    await userEvent.click(loading);

    expect(loading).toBeDisabled();
    expect(asked().filter((a) => a.startsWith("active:100"))).toHaveLength(1);
    release(reply(200, []));
    await waitFor(() => expect(screen.queryByRole("button", { name: /load/i })).not.toBeInTheDocument());
  });

  test("a failure keeps what was loaded, says so, and lets the owner try again", async () => {
    let fail = true;
    fetchMock.mockImplementation(async (input) => {
      const offset = new URL(String(input), "http://test").searchParams.get("offset");
      if (offset === "0") return reply(200, many(100));
      return fail ? reply(500, "boom") : reply(200, many(3, 101));
    });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Memory number 100.")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Memory number 103.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("the filter box", () => {
  const rows = [
    memory(1, { content: "The owner lives in Porto." }),
    memory(2, { content: "Still water only.", kind: "preference" }),
    memory(3, { content: "Dentist on 12 November.", kind: "event" }),
  ];

  test("narrows what is loaded, says how many match, and clears", async () => {
    serve({ "active:0": rows });
    render(<MemoryList />);
    await screen.findByText("Still water only.");

    await userEvent.type(screen.getByRole("searchbox", { name: "Filter memories" }), "porto");

    expect(screen.getByText("The owner lives in Porto.")).toBeInTheDocument();
    expect(screen.queryByText("Still water only.")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("1 of 3 loaded match");

    await userEvent.clear(screen.getByRole("searchbox"));
    expect(screen.getByText("Still water only.")).toBeInTheDocument();
  });

  test("matches the kind too", async () => {
    serve({ "active:0": rows });
    render(<MemoryList />);
    await screen.findByText("Still water only.");

    await userEvent.type(screen.getByRole("searchbox"), "event");

    expect(screen.getByText("Dentist on 12 November.")).toBeInTheDocument();
    expect(screen.queryByText("The owner lives in Porto.")).not.toBeInTheDocument();
  });

  test("with nothing matching says so, and points to Load more only when there is more", async () => {
    serve({ "active:0": many(100) });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await userEvent.type(screen.getByRole("searchbox"), "zebra");

    expect(screen.getByText(/No loaded memory matches/)).toHaveTextContent("Load more to search further");
  });

  test("with nothing matching and nothing more to load, does not suggest loading more", async () => {
    serve({ "active:0": rows });
    render(<MemoryList />);
    await screen.findByText("Still water only.");

    await userEvent.type(screen.getByRole("searchbox"), "zebra");

    expect(screen.getByText(/No loaded memory matches/)).not.toHaveTextContent("Load more");
  });

  test("is one box for every tab", async () => {
    serve({ "active:0": rows, "all:0": rows });
    render(<MemoryList />);
    await screen.findByText("Still water only.");
    await userEvent.type(screen.getByRole("searchbox"), "porto");

    await userEvent.click(tab("All"));

    expect(await screen.findAllByText("The owner lives in Porto.")).not.toHaveLength(0);
    expect(screen.getByRole("searchbox")).toHaveValue("porto");
    expect(screen.queryByText("Still water only.")).not.toBeInTheDocument();
  });
});

describe("a failure to load", () => {
  test("is said, with a way to try again", async () => {
    serve({ "active:0": reply(500, "boom") });
    render(<MemoryList />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the memories");

    serve({ "active:0": [memory(1)] });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Memory number 1.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("on one tab leaves the others alone", async () => {
    serve({ "active:0": [memory(1)], "deleted:0": reply(500, "boom") });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await userEvent.click(tab("Deleted"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await userEvent.click(tab("Active"));

    expect(screen.getByText("Memory number 1.")).toBeVisible();
  });
});
