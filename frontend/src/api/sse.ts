import type { StreamEvent } from "./types";

// Reads the events of a turn from the body of a POST response. `EventSource` cannot POST, so the
// stream is parsed here (docs/PHASE4.md, "Streaming").
//
// Frames end at a blank line. Each has an `event` and a `data` line. The reader copes with what a
// network does to that: a frame or a multi-byte character split across chunks, `\r\n` line endings
// (a `\r` and its `\n` may even arrive in different chunks), comment lines, events it does not
// know, a last frame without its blank line, and a stream that stops before `done` or `error`.

export const LOST = {
  type: "error",
  code: "connection_lost",
  message: "The connection was lost. Reload to see whether the reply was saved.",
} as const satisfies StreamEvent;

const UNREADABLE = {
  type: "error",
  code: "bad_stream",
  message: "The reply could not be read.",
} as const satisfies StreamEvent;

type Payload = Record<string, unknown>;

const isObject = (value: unknown): value is Payload =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isList = (value: unknown) => Array.isArray(value);
const isNumber = (value: unknown) => typeof value === "number";
const isString = (value: unknown) => typeof value === "string";

// What each event must carry. Anything else is a stream the app cannot trust
const SHAPES: Record<StreamEvent["type"], Record<string, (value: unknown) => boolean>> = {
  memory: { current: isList, past: isList },
  text: { text: isString },
  done: {
    message_id: isNumber,
    input_tokens: isNumber,
    output_tokens: isNumber,
    steps: isNumber,
    recall_seconds: isNumber,
    total_seconds: isNumber,
  },
  error: { code: isString, message: isString },
};

function isKnown(name: string): name is StreamEvent["type"] {
  return Object.hasOwn(SHAPES, name);
}

type Parsed = StreamEvent | "skip" | "unreadable";

function parseFrame(frame: string): Parsed {
  let name = "";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") name = value;
    else if (field === "data") data.push(value);
  }
  if (!name || !isKnown(name)) return "skip";

  let payload: unknown;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    return "unreadable";
  }
  if (!isObject(payload)) return "unreadable";
  const shape = SHAPES[name];
  if (!Object.entries(shape).every(([key, check]) => check(payload[key]))) return "unreadable";
  return { ...payload, type: name } as StreamEvent;
}

function isAbort(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as Error).name === "AbortError";
}

/**
 * The events of one turn, in order. It always ends with `done` or `error`: a stream that stops
 * without either yields a `connection_lost` error, and a frame that cannot be read ends the turn
 * with `bad_stream`. Aborting the request throws its AbortError, for the caller to handle.
 */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let held = ""; // a trailing "\r" waits for the next chunk, which may begin with its "\n"
  let text = "";
  let finished = false;

  try {
    while (!finished) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (error) {
        if (isAbort(error)) throw error;
        yield LOST;
        return;
      }

      let raw = held + (chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true }));
      held = "";
      if (!chunk.done && raw.endsWith("\r")) {
        held = "\r";
        raw = raw.slice(0, -1);
      }
      text += raw.replace(/\r\n?/g, "\n");

      for (let end = text.indexOf("\n\n"); end !== -1; end = text.indexOf("\n\n")) {
        const parsed = parseFrame(text.slice(0, end));
        text = text.slice(end + 2);
        if (parsed === "skip") continue;
        if (parsed === "unreadable") {
          yield UNREADABLE;
          return;
        }
        yield parsed;
        if (parsed.type === "done" || parsed.type === "error") {
          finished = true;
          break;
        }
      }

      if (chunk.done) {
        // A last frame the server did not finish with a blank line still counts if it is whole
        if (!finished && text.trim()) {
          const parsed = parseFrame(text);
          if (parsed !== "skip" && parsed !== "unreadable") {
            yield parsed;
            finished = parsed.type === "done" || parsed.type === "error";
          }
        }
        break;
      }
    }
    if (!finished) yield LOST;
  } finally {
    // Stops the download if the consumer left early, and is harmless after the end
    reader.cancel().catch(() => {});
  }
}
