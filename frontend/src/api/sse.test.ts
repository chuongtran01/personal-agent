import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, test } from "vitest";

import { LOST, readEvents } from "./sse";
import type { StreamEvent } from "./types";

// Tests run from frontend/, where the fixtures recorded from the real API are kept
const fixture = (name: string) =>
  new Uint8Array(readFileSync(path.join(process.cwd(), "src/test/fixtures", name)));
const encode = (text: string) => new TextEncoder().encode(text);

/** A body that delivers `bytes` in pieces of `size`, as a network might. */
function bodyOf(bytes: Uint8Array, size = bytes.length): ReadableStream<Uint8Array> {
  let at = 0;
  return new ReadableStream({
    pull(controller) {
      if (at >= bytes.length) return controller.close();
      controller.enqueue(bytes.slice(at, at + size));
      at += size;
    },
  });
}

async function collect(body: ReadableStream<Uint8Array>): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const event of readEvents(body)) events.push(event);
  return events;
}

const frame = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
const DONE = {
  message_id: 5,
  input_tokens: 10,
  output_tokens: 4,
  steps: 1,
  recall_seconds: 0.1,
  total_seconds: 1,
};

// A turn recorded from the real API ----------------------------------------------------------

describe("a recorded turn", () => {
  test("is memory, then text, then done", async () => {
    const events = await collect(bodyOf(fixture("turn.sse")));

    expect(events[0].type).toBe("memory");
    expect(events.at(-1)?.type).toBe("done");
    const text = events.flatMap((e) => (e.type === "text" ? [e.text] : [])).join("");
    expect(text).toContain("Porto");
    const memory = events[0];
    if (memory.type !== "memory") throw new Error("not a memory event");
    expect(memory.current[0]).toMatchObject({ id: 1, kind: "fact" });
    expect(memory.past).toEqual([]);
    expect(events.filter((e) => e.type === "error")).toEqual([]);
  });

  test("a question about the past brings the replaced memories in `past`", async () => {
    const events = await collect(bodyOf(fixture("history-turn.sse")));

    const memory = events[0];
    if (memory.type !== "memory") throw new Error("not a memory event");
    expect(memory.past.length).toBeGreaterThan(0);
    expect(memory.past[0]).toHaveProperty("valid_until");
  });

  test.each([1, 2, 3, 5, 7, 16, 64, 1000])(
    "gives the same events whatever size the chunks are (%i bytes)",
    async (size) => {
      for (const name of ["turn.sse", "history-turn.sse"]) {
        const whole = await collect(bodyOf(fixture(name)));
        expect(await collect(bodyOf(fixture(name), size))).toEqual(whole);
      }
    },
  );

  test.each([1, 2, 3, 7])("copes with \\r\\n line endings, split anywhere (%i bytes)", async (size) => {
    const lf = new TextDecoder().decode(fixture("turn.sse"));
    const crlf = encode(lf.replaceAll("\n", "\r\n"));

    expect(await collect(bodyOf(crlf, size))).toEqual(await collect(bodyOf(fixture("turn.sse"))));
  });
});

// What a network does to a frame -------------------------------------------------------------

describe("a frame", () => {
  test("keeps a multi-byte character that is split across chunks", async () => {
    const text = "café — 日本語 🙂 naïve";
    const bytes = encode(frame("text", { text }) + frame("done", DONE));

    const events = await collect(bodyOf(bytes, 1));

    expect(events[0]).toEqual({ type: "text", text });
  });

  test("may carry its data on several lines", async () => {
    const bytes = encode('event: text\ndata: {"text":\ndata: "two lines"}\n\n' + frame("done", DONE));

    expect((await collect(bodyOf(bytes)))[0]).toEqual({ type: "text", text: "two lines" });
  });

  test("needs no space after the colon", async () => {
    const bytes = encode('event:text\ndata:{"text":"x"}\n\n' + frame("done", DONE));

    expect((await collect(bodyOf(bytes)))[0]).toEqual({ type: "text", text: "x" });
  });

  test("comment lines and events it does not know are skipped", async () => {
    const bytes = encode(
      ": keep-alive\n\n" +
        frame("progress", { percent: 50 }) +
        ": a comment inside\nevent: text\ndata: {\"text\":\"kept\"}\n\n" +
        frame("done", DONE),
    );

    const events = await collect(bodyOf(bytes));

    expect(events.map((e) => e.type)).toEqual(["text", "done"]);
  });

  test("a last frame without its blank line still counts when it is whole", async () => {
    const bytes = encode(frame("text", { text: "a" }) + `event: done\ndata: ${JSON.stringify(DONE)}`);

    const events = await collect(bodyOf(bytes));

    expect(events.map((e) => e.type)).toEqual(["text", "done"]);
  });

  test("a last frame cut in the middle is a lost connection, not a broken one", async () => {
    const bytes = encode(frame("text", { text: "a" }) + 'event: text\ndata: {"text":"cut of');

    expect(await collect(bodyOf(bytes))).toEqual([{ type: "text", text: "a" }, LOST]);
  });
});

// How a turn ends ---------------------------------------------------------------------------

describe("the end of a turn", () => {
  test("an error event ends it, and nothing is added after it", async () => {
    const bytes = encode(
      frame("memory", { current: [], past: [] }) +
        frame("error", { code: "model_error", message: "the model could not answer" }),
    );

    const events = await collect(bodyOf(bytes));

    expect(events.map((e) => e.type)).toEqual(["memory", "error"]);
    expect(events.at(-1)).toMatchObject({ code: "model_error" });
  });

  test("a stream that stops before done or error is a lost connection", async () => {
    const bytes = encode(frame("memory", { current: [], past: [] }) + frame("text", { text: "par" }));

    const events = await collect(bodyOf(bytes));

    expect(events.map((e) => e.type)).toEqual(["memory", "text", "error"]);
    expect(events.at(-1)).toEqual(LOST);
  });

  test("an empty body is a lost connection", async () => {
    expect(await collect(bodyOf(new Uint8Array()))).toEqual([LOST]);
  });

  test("bytes after done are not read", async () => {
    const bytes = encode(frame("done", DONE) + frame("text", { text: "late" }));

    const events = await collect(bodyOf(bytes));

    expect(events.map((e) => e.type)).toEqual(["done"]);
  });

  test("a network failure part-way is a lost connection", async () => {
    let sent = false;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent) throw new TypeError("network error");
        sent = true;
        controller.enqueue(encode(frame("text", { text: "a" })));
      },
    });

    const events = await collect(body);

    expect(events).toEqual([{ type: "text", text: "a" }, LOST]);
  });

  test("aborting the request throws, for the caller to treat as a choice", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encode(frame("text", { text: "a" })));
      },
      pull(controller) {
        controller.error(new DOMException("aborted", "AbortError"));
      },
    });

    await expect(collect(body)).rejects.toMatchObject({ name: "AbortError" });
  });

  test("a consumer that stops early stops the download", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(encode(frame("text", { text: "a" })));
      },
      cancel() {
        cancelled = true;
      },
    });

    for await (const event of readEvents(body)) {
      expect(event.type).toBe("text");
      break;
    }

    expect(cancelled).toBe(true);
  });
});

// A stream that cannot be trusted ------------------------------------------------------------

describe("an unreadable frame", () => {
  const cases: Array<[string, string]> = [
    ["data that is not JSON", "event: text\ndata: {oops\n\n"],
    ["data that is not an object", 'event: text\ndata: "just a string"\n\n'],
    ["a text event without its text", frame("text", { words: "x" })],
    ["a done event with a wrong field type", frame("done", { ...DONE, message_id: "five" })],
    ["a memory event whose lists are not lists", frame("memory", { current: 1, past: [] })],
  ];

  test.each(cases)("%s ends the turn as bad_stream", async (_, wire) => {
    const events = await collect(bodyOf(encode(wire + frame("done", DONE))));

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "error", code: "bad_stream" });
  });
});
