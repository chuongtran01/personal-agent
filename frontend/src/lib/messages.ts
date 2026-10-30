// What the owner reads for each error code the API client gives (api/http.ts and the endpoint
// modules). One table, so a code reads the same wherever it shows up.

const MESSAGES: Record<string, string> = {
  wrong_password: "That password is not right.",
  login_not_configured: "Login is not available: the server is not set up.",
  unauthorized: "Your session has ended. Please log in again.",
  network: "Cannot reach the server.",
};

const FALLBACK = "Something went wrong. Please try again.";

export function messageFor(code: string): string {
  return MESSAGES[code] ?? FALLBACK;
}
