// What the owner reads for each error code the API client gives (api/http.ts and the endpoint
// modules). One table, so a code reads the same wherever it shows up.

const MESSAGES: Record<string, string> = {
  wrong_password: "That password is not right.",
  login_not_configured: "Login is not available: the server is not set up.",
  unauthorized: "Your session has ended. Please log in again.",
  network: "Cannot reach the server.",
  bad_stream: "The reply could not be read.",
  connection_lost: "The connection was lost. Reload to see whether the reply was saved.",
  // Sending a message (the server's refusals and the assistant being unavailable)
  chat_full: "This chat is full. Start a new one.",
  message_too_long: "That message is too long.",
  empty_message: "Type a message first.",
  turn_in_progress: "Still answering the last message. Try again in a moment.",
  session_not_found: "That chat no longer exists.",
  invalid_request: "That chat no longer exists.",
  agent_not_configured: "The assistant is not available: the server has no model key.",
};

const FALLBACK = "Something went wrong. Please try again.";

export function messageFor(code: string): string {
  return MESSAGES[code] ?? FALLBACK;
}
