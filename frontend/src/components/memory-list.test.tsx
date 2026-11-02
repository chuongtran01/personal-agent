import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, test } from "vitest";

import type { Memory, MemoryHistoryEntry } from "@/api/types";
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

  test("says how to correct a memory: tell the assistant", async () => {
    serve({ "active:0": [memory(1)] });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    expect(screen.getByText(/tell the assistant/i)).toBeInTheDocument();
    expect(screen.queryByText(/view only/i)).not.toBeInTheDocument();
  });

  test("has no way to edit a memory or bring a deleted one back", async () => {
    serve({
      "active:0": [memory(1)],
      "deleted:0": [memory(2, { status: "deleted" })],
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(tab("Deleted"));
    await screen.findByText("Memory number 2.");

    expect(screen.queryByRole("button", { name: /edit|restore|undo/i })).not.toBeInTheDocument();
  });
});

describe("a memory's row", () => {
  test("shows its kind, its text, when it was made, and that it was never shown", async () => {
    serve({
      "active:0": [memory(1, { kind: "preference", content: "Still water only." })],
    });
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
    serve({
      "active:0": [memory(1, { valid_from: "2027-01-04T12:00:00Z" }), memory(2)],
    });
    render(<MemoryList />);

    const rows = await screen.findAllByRole("listitem");

    expect(rows[0]).toHaveTextContent("Holds from Jan 4, 2027");
    expect(rows[1]).not.toHaveTextContent("Holds");
  });

  test("marks one that did not come from the owner", async () => {
    serve({
      "active:0": [memory(1, { source_kind: "external", content: "From an email." })],
    });
    render(<MemoryList />);

    const row = (await screen.findByText("From an email.")).closest("li")!;

    expect(within(row).getByText("external")).toBeInTheDocument();
  });

  test("a replaced memory says it was, and by which", async () => {
    serve({
      "superseded:0": [
        memory(3, {
          status: "superseded",
          superseded_by: 9,
          content: "Lived in Lisbon.",
        }),
      ],
    });
    render(<MemoryList />);
    await userEvent.click(tab("Replaced"));

    const row = (await screen.findByText("Lived in Lisbon.")).closest("li")!;

    expect(within(row).getByText("replaced")).toBeInTheDocument();
    expect(row).toHaveTextContent("Replaced by memory 9");
  });

  test("a deleted memory is shown, softened, under its own tab", async () => {
    serve({
      "deleted:0": [memory(4, { status: "deleted", content: "Something forgotten." })],
    });
    render(<MemoryList />);
    await userEvent.click(tab("Deleted"));

    const text = await screen.findByText("Something forgotten.");

    expect(within(text.closest("li")!).getByText("deleted")).toBeInTheDocument();
    expect(text).toHaveClass("text-muted-foreground");
  });
});

describe("the tabs", () => {
  test("each asks for its own status, and All for every status", async () => {
    serve({
      "active:0": [memory(1)],
      "superseded:0": [],
      "deleted:0": [],
      "all:0": [memory(1)],
    });
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

describe("a memory's text is only ever text", () => {
  const hostile =
    "**bold** [click](https://evil.example) ![pixel](https://evil.example/p.png) <img src=x onerror=alert(1)> <script>alert(2)</script>";

  test("in the list too: no formatting, no link, no image, no script", async () => {
    serve({
      "active:0": [memory(1, { content: hostile, source_kind: "external" })],
    });
    const { container } = render(<MemoryList />);

    expect(await screen.findByText(hostile)).toBeInTheDocument();

    expect(container.querySelector("li strong, li a, li img, li script")).toBeNull();
  });
});

// Deleting and the history ------------------------------------------------------------------------

interface Api {
  pages?: Record<string, Memory[] | Response>;
  /** what DELETE /memories/{id} answers; 204 when absent */
  onDelete?: (id: number) => Response | Promise<Response>;
  /** what GET /memories/{id}/events answers */
  events?: (id: number) => Response | Promise<Response>;
}

/** The whole memory API: the list as `serve` does, and the delete and the events by id. */
function serveApi({ pages = {}, onDelete, events }: Api = {}) {
  fetchMock.mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const one = url.pathname.match(/^\/api\/memories\/(\d+)(\/events)?$/);
    if (one && method === "DELETE") {
      return onDelete ? onDelete(Number(one[1])) : new Response(null, { status: 204 });
    }
    if (one && one[2]) return events ? events(Number(one[1])) : reply(200, []);
    const key = `${url.searchParams.get("status") ?? "all"}:${url.searchParams.get("offset")}`;
    const page = pages[key];
    return page instanceof Response ? page : reply(200, page ?? []);
  });
}
const deletes = () =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE").map(([input]) => String(input));
const eventCalls = () =>
  fetchMock.mock.calls.map(([input]) => String(input)).filter((url) => url.endsWith("/events"));
const entry = (id: number, overrides: Partial<MemoryHistoryEntry> = {}): MemoryHistoryEntry => ({
  id,
  event: "created",
  reason: "the owner said it",
  message_id: null,
  created_at: "2026-08-01T12:00:00Z",
  ...overrides,
});
const deleteButton = (n: number) => screen.getByRole("button", { name: `Delete: Memory number ${n}.` });
const historyButton = (n: number) => screen.getByRole("button", { name: `History of: Memory number ${n}.` });

describe("which memories can be deleted", () => {
  test("an active one and a replaced one can; a deleted one cannot", async () => {
    serveApi({
      pages: {
        "active:0": [memory(1)],
        "superseded:0": [memory(2, { status: "superseded", superseded_by: 3 })],
        "deleted:0": [memory(4, { status: "deleted" })],
      },
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    expect(deleteButton(1)).toBeEnabled();

    await user.click(tab("Replaced"));
    await screen.findByText("Memory number 2.");
    expect(deleteButton(2)).toBeEnabled();

    await user.click(tab("Deleted"));
    await screen.findByText("Memory number 4.");
    expect(screen.queryByRole("button", { name: "Delete: Memory number 4." })).not.toBeInTheDocument();
    expect(historyButton(4)).toBeInTheDocument();
  });

  test("each button is named after its memory, so a long list can be told apart", async () => {
    serveApi({ pages: { "active:0": [memory(1), memory(2)] } });
    render(<MemoryList />);
    await screen.findByText("Memory number 2.");

    expect(deleteButton(1)).not.toBe(deleteButton(2));
    expect(screen.getAllByRole("button", { name: /^Delete: / })).toHaveLength(2);
  });
});

describe("deleting a memory", () => {
  test("asks first, says what will happen, and sends nothing yet", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await user.click(deleteButton(1));

    const group = screen.getByRole("group", {
      name: "Confirm deleting: Memory number 1.",
    });
    expect(group).toHaveTextContent("This memory will not be used again. It stays listed under Deleted.");
    expect(deletes()).toEqual([]);
    expect(screen.getByText("Memory number 1.")).toBeInTheDocument();
  });

  test("puts focus on Cancel, so Enter cannot delete by accident", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await user.click(deleteButton(1));

    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(deletes()).toEqual([]);
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });

  test("Cancel closes the question and gives focus back to Delete", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(deleteButton(1)).toHaveFocus();
    expect(deleteButton(1)).toBeEnabled();
    expect(deletes()).toEqual([]);
  });

  test("Escape does the same as Cancel", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(deleteButton(1)).toHaveFocus();
  });

  test("while the question is open, Delete says it is expanded and pressing it again changes nothing", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await user.click(deleteButton(1));
    expect(deleteButton(1)).toHaveAttribute("aria-expanded", "true");
    await user.click(deleteButton(1));

    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(deletes()).toEqual([]);
  });

  test("confirming sends one DELETE for that memory", async () => {
    serveApi({ pages: { "active:0": [memory(1), memory(2)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 2.");
    await user.click(deleteButton(2));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    await waitFor(() => expect(deletes()).toEqual(["/api/memories/2"]));
  });

  test("the row leaves the Active tab at once, and the count follows", async () => {
    serveApi({ pages: { "active:0": [memory(1), memory(2)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 2.");
    await user.click(deleteButton(1));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    await waitFor(() => expect(screen.queryByText("Memory number 1.")).not.toBeInTheDocument());
    expect(screen.getByText("Memory number 2.")).toBeInTheDocument();
    expect(screen.getByText("1 loaded")).toBeInTheDocument();
  });

  test("says it was deleted, in a message that is announced and has focus", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    const notice = await screen.findByText(/Memory deleted\./);
    expect(notice).toHaveAttribute("role", "status");
    expect(notice).toHaveFocus();
    expect(notice).toHaveTextContent("It will not be used again, and is listed under Deleted.");
  });

  test("the last memory deleted leaves the empty message, not a blank page", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    expect(await screen.findByText("No active memories yet.")).toBeInTheDocument();
  });

  test("shows at the top of the Deleted tab straight away, before the server has been asked", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));
    await user.click(screen.getByRole("button", { name: "Delete memory" }));
    await screen.findByText(/Memory deleted\./);
    fetchMock.mockImplementation(() => new Promise<Response>(() => {})); // the server never answers

    await user.click(tab("Deleted"));

    const row = screen.getByText("Memory number 1.").closest("li")!;
    expect(row).toHaveTextContent("deleted");
    expect(within(row).queryByRole("button", { name: /^Delete: / })).not.toBeInTheDocument();
  });

  test("is not shown twice on the Deleted tab once the server lists it too", async () => {
    serveApi({
      pages: {
        "active:0": [memory(1)],
        "deleted:0": [memory(1, { status: "deleted" }), memory(5, { status: "deleted" })],
      },
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));
    await user.click(screen.getByRole("button", { name: "Delete memory" }));
    await screen.findByText(/Memory deleted\./);

    await user.click(tab("Deleted"));
    await screen.findByText("Memory number 5.");

    expect(screen.getAllByText("Memory number 1.")).toHaveLength(1);
  });

  test("the newest deletion is first on the Deleted tab", async () => {
    serveApi({ pages: { "active:0": [memory(1), memory(2)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 2.");
    await user.click(deleteButton(1));
    await user.click(screen.getByRole("button", { name: "Delete memory" }));
    await waitFor(() => expect(screen.queryByText("Memory number 1.")).not.toBeInTheDocument());
    await user.click(deleteButton(2));
    await user.click(screen.getByRole("button", { name: "Delete memory" }));
    await screen.findByText("No active memories yet.");

    await user.click(tab("Deleted"));

    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Memory number 2.");
    expect(rows[1]).toHaveTextContent("Memory number 1.");
  });

  test("on the All tab the row stays, now marked deleted and with no Delete button", async () => {
    serveApi({
      pages: { "active:0": [memory(1)], "all:0": [memory(1), memory(2)] },
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(tab("All"));
    await screen.findAllByText("Memory number 2.");
    const activePanel = screen.getByRole("tabpanel", { name: "Active" });
    await user.click(
      within(activePanel).getByRole("button", {
        name: "Delete: Memory number 1.",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Delete memory" }));
    await screen.findByText(/Memory deleted\./);

    const allPanel = screen.getByRole("tabpanel", { name: "All" });
    const row = within(allPanel).getByText("Memory number 1.").closest("li")!;

    expect(within(row).getByText("deleted")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /^Delete: / })).not.toBeInTheDocument();
    expect(
      within(allPanel).getByRole("button", {
        name: "Delete: Memory number 2.",
      }),
    ).toBeInTheDocument();
  });

  test("a replaced memory can be deleted, and then leaves the Replaced tab", async () => {
    serveApi({
      pages: {
        "superseded:0": [memory(2, { status: "superseded", superseded_by: 3 })],
      },
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await user.click(tab("Replaced"));
    await screen.findByText("Memory number 2.");
    await user.click(deleteButton(2));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    expect(await screen.findByText("Nothing has been replaced yet.")).toBeInTheDocument();
    expect(deletes()).toEqual(["/api/memories/2"]);
  });

  test("while it is on its way the buttons are locked, and a second click sends nothing more", async () => {
    let answer: (response: Response) => void = () => {};
    serveApi({
      pages: { "active:0": [memory(1)] },
      onDelete: () => new Promise<Response>((resolve) => (answer = resolve)),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    const working = await screen.findByRole("button", { name: "Deleting…" });
    expect(working).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("group"), { key: "Escape" });
    expect(screen.getByRole("group")).toBeInTheDocument(); // Escape does not abandon a request in flight
    expect(deletes()).toHaveLength(1);
    answer(new Response(null, { status: 204 }));
    await waitFor(() => expect(screen.queryByText("Memory number 1.")).not.toBeInTheDocument());
    expect(deletes()).toHaveLength(1);
  });

  test("a failure says so, keeps the memory, and leaves the owner able to try again or cancel", async () => {
    let fail = true;
    serveApi({
      pages: { "active:0": [memory(1)] },
      onDelete: () => (fail ? reply(500, "boom") : new Response(null, { status: 204 })),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    expect(await screen.findByText("Something went wrong. Please try again.")).toBeInTheDocument();
    expect(screen.getByText("Memory number 1.")).toBeInTheDocument();
    expect(screen.queryByText(/Memory deleted\./)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete memory" })).toBeEnabled();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Delete memory" }));
    await waitFor(() => expect(screen.queryByText("Memory number 1.")).not.toBeInTheDocument());
  });

  test("a memory that is already gone says so", async () => {
    serveApi({
      pages: { "active:0": [memory(1)] },
      onDelete: () =>
        reply(404, {
          detail: { code: "memory_not_found", message: "no memory 1" },
        }),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    expect(await screen.findByText("That memory no longer exists. Reload the list.")).toBeInTheDocument();
  });

  test("a lost connection says so", async () => {
    serveApi({
      pages: { "active:0": [memory(1)] },
      onDelete: () => Promise.reject(new TypeError("Failed to fetch")),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));

    await user.click(screen.getByRole("button", { name: "Delete memory" }));

    expect(await screen.findByText("Cannot reach the server.")).toBeInTheDocument();
  });

  test("cancelling after a failure clears the message", async () => {
    serveApi({
      pages: { "active:0": [memory(1)] },
      onDelete: () => reply(500, "boom"),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(deleteButton(1));
    await user.click(screen.getByRole("button", { name: "Delete memory" }));
    await screen.findByText("Something went wrong. Please try again.");

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByText("Something went wrong. Please try again.")).not.toBeInTheDocument();
  });

  test("the whole flow works with the keyboard alone", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    deleteButton(1).focus();
    await user.keyboard("{Enter}"); // opens the question, and focus lands on Cancel
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Delete memory" })).toHaveFocus();
    await user.keyboard("{Enter}");

    await screen.findByText(/Memory deleted\./);
    expect(deletes()).toEqual(["/api/memories/1"]);
  });
});

describe("a memory's history", () => {
  test("is not read until it is opened", async () => {
    serveApi({ pages: { "active:0": [memory(1)] } });
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    expect(eventCalls()).toEqual([]);
    expect(historyButton(1)).toHaveAttribute("aria-expanded", "false");
  });

  test("opening it reads that memory's events and lists them, oldest first, in words", async () => {
    serveApi({
      pages: { "active:0": [memory(1), memory(2)] },
      events: () =>
        reply(200, [
          entry(1, { reason: "the owner said it", message_id: 9 }),
          entry(2, {
            event: "superseded",
            reason: "the owner's new statement replaced it",
            created_at: "2026-09-02T12:00:00Z",
          }),
        ]),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 2.");

    await user.click(historyButton(2));

    const list = await screen.findByRole("list", { name: "History" });
    const lines = within(list).getAllByRole("listitem");
    expect(lines.map((line) => line.textContent)).toEqual([
      "Written Aug 1, 2026 · The owner said it · from a chat message",
      "Replaced Sep 2, 2026 · The owner's new statement replaced it",
    ]);
    expect(eventCalls()).toEqual(["/api/memories/2/events"]);
    expect(historyButton(2)).toHaveAttribute("aria-expanded", "true");
  });

  test("shows placeholders while it is read", async () => {
    serveApi({
      pages: { "active:0": [memory(1)] },
      events: () => new Promise<Response>(() => {}),
    });
    const user = userEvent.setup();
    const { container } = render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    const before = container.querySelectorAll("[data-slot=skeleton]").length;

    await user.click(historyButton(1));

    expect(container.querySelectorAll("[data-slot=skeleton]").length).toBeGreaterThan(before);
    expect(screen.queryByRole("list", { name: "History" })).not.toBeInTheDocument();
  });

  test("closing and opening it again keeps what was read and does not ask again", async () => {
    serveApi({
      pages: { "active:0": [memory(1)] },
      events: () => reply(200, [entry(1)]),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(historyButton(1));
    await screen.findByRole("list", { name: "History" });

    await user.click(historyButton(1));
    expect(screen.queryByRole("list", { name: "History" })).not.toBeInTheDocument();
    await user.click(historyButton(1));

    expect(await screen.findByRole("list", { name: "History" })).toBeInTheDocument();
    expect(eventCalls()).toHaveLength(1);
  });

  test("a failure says so and Try again asks again", async () => {
    let fail = true;
    serveApi({
      pages: { "active:0": [memory(1)] },
      events: () => (fail ? reply(500, "boom") : reply(200, [entry(1)])),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(historyButton(1));

    expect(await screen.findByText(/Could not load the history\./)).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("list", { name: "History" })).toBeInTheDocument();
    expect(eventCalls()).toHaveLength(2);
  });

  test("a failure then closing and opening it asks again by itself", async () => {
    let fail = true;
    serveApi({
      pages: { "active:0": [memory(1)] },
      events: () => (fail ? reply(500, "boom") : reply(200, [entry(1)])),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(historyButton(1));
    await screen.findByText(/Could not load the history\./);
    await user.click(historyButton(1));
    fail = false;

    await user.click(historyButton(1));

    expect(await screen.findByRole("list", { name: "History" })).toBeInTheDocument();
  });

  test("is plain text, whatever the server's reason says", async () => {
    serveApi({
      pages: { "active:0": [memory(1)] },
      events: () => reply(200, [entry(1, { reason: "<img src=x onerror=alert(1)> **bold**" })]),
    });
    const user = userEvent.setup();
    const { container } = render(<MemoryList />);
    await screen.findByText("Memory number 1.");

    await user.click(historyButton(1));

    const list = await screen.findByRole("list", { name: "History" });
    expect(list).toHaveTextContent("<img src=x onerror=alert(1)> **bold**");
    expect(container.querySelector("img, strong")).toBeNull();
  });

  test("is there for a deleted memory too, and each memory reads its own", async () => {
    serveApi({
      pages: {
        "deleted:0": [memory(4, { status: "deleted" })],
        "active:0": [memory(1)],
      },
      events: (id) => reply(200, [entry(1, { reason: `history of ${id}` })]),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(historyButton(1));
    await screen.findByText(/History of 1/i);
    await user.click(tab("Deleted"));
    await screen.findByText("Memory number 4.");

    await user.click(historyButton(4));

    expect(await screen.findByText(/History of 4/i)).toBeInTheDocument();
    expect(eventCalls()).toEqual(["/api/memories/1/events", "/api/memories/4/events"]);
  });

  test("after a delete it is read afresh, so it shows the deletion", async () => {
    let deleted = false;
    serveApi({
      pages: { "active:0": [memory(1)], "all:0": [memory(1)] },
      onDelete: () => {
        deleted = true;
        return new Response(null, { status: 204 });
      },
      events: () =>
        reply(
          200,
          deleted
            ? [
                entry(1),
                entry(2, {
                  event: "deleted",
                  reason: "deleted by the owner in the app",
                }),
              ]
            : [entry(1)],
        ),
    });
    const user = userEvent.setup();
    render(<MemoryList />);
    await screen.findByText("Memory number 1.");
    await user.click(tab("All"));
    const allPanel = screen.getByRole("tabpanel", { name: "All" });
    await within(allPanel).findByText("Memory number 1.");
    await user.click(
      within(allPanel).getByRole("button", {
        name: "History of: Memory number 1.",
      }),
    );
    await within(allPanel).findByRole("list", { name: "History" });
    await user.click(
      within(allPanel).getByRole("button", {
        name: "Delete: Memory number 1.",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Delete memory" }));
    await screen.findByText(/Memory deleted\./);

    await user.click(
      within(allPanel).getByRole("button", {
        name: "History of: Memory number 1.",
      }),
    );

    const list = await within(allPanel).findByRole("list", { name: "History" });
    expect(list).toHaveTextContent("Deleted Aug 1, 2026 · Deleted by the owner in the app");
  });
});
