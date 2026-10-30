export const frame = (name: string, data: unknown) =>
  `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

/** A reply the test feeds by hand, one event at a time. */
export function liveReply() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: () => new Response(body, { status: 200 }),
    push: (name: string, data: unknown) => controller.enqueue(encoder.encode(frame(name, data))),
    end: () => controller.close(),
  };
}

export const DONE = {
  message_id: 41,
  input_tokens: 10,
  output_tokens: 4,
  steps: 1,
  recall_seconds: 0.1,
  total_seconds: 1,
};

export const MEMORY = {
  current: [
    { id: 1, kind: "fact", content: "The owner lives in Porto.", valid_from: null, valid_until: null },
  ],
  past: [],
};
