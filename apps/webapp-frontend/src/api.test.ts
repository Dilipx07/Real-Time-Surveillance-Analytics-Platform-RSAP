import { describe, expect, it } from "vitest";
import { authHeaders, type AuthSession } from "./api";

describe("authHeaders", () => {
  it("includes both central auth tokens", () => {
    const session = {
      access_token: "jwt",
      refresh_token: "refresh",
      session_token: "session",
      token_type: "bearer",
      expires_in: 900,
      user: { id: "u1", email: "admin@rsap.local", role: "super_admin" }
    } satisfies AuthSession;

    expect(authHeaders(session)).toEqual({
      Authorization: "Bearer jwt",
      "X-Session-Token": "session"
    });
  });
});
