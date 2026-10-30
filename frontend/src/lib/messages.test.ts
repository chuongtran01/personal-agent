import { expect, test } from "vitest";

import { messageFor } from "./messages";

test.each([
  "wrong_password",
  "login_not_configured",
  "unauthorized",
  "network",
  "chat_full",
  "message_too_long",
  "empty_message",
  "turn_in_progress",
  "session_not_found",
  "agent_not_configured",
])(
  "the code %s has a message of its own",
  (code) => {
    expect(messageFor(code)).not.toBe(messageFor("no_such_code"));
  },
);

test("an unknown code gets a plain fallback that says nothing about internals", () => {
  expect(messageFor("something_new")).toBe("Something went wrong. Please try again.");
});
