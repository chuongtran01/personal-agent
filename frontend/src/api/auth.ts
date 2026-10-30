import { clearToken, setToken } from "@/auth/token";

import { renaming, request } from "./http";
import type { LoginResponse } from "./types";

/** Keeps the token for its lifetime. A wrong password is `wrong_password`, login with no password
 *  or secret set up on the server is `login_not_configured`; neither signs anyone out. */
export async function login(password: string): Promise<void> {
  const response = await renaming(401, "wrong_password", () =>
    renaming(503, "login_not_configured", () =>
      request("/auth/login", { method: "POST", body: { password }, auth: false }),
    ),
  );
  const body = (await response.json()) as LoginResponse;
  setToken(body.access_token, body.expires_in);
}

export function logout(): void {
  clearToken();
}
