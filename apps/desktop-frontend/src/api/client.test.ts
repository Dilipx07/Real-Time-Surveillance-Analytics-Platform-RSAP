import { describe, expect, it, vi } from "vitest";
import { ApiError, DesktopApiClient } from "./client";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init
  });
}

describe("DesktopApiClient", () => {
  it("parses a success envelope", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ success: true, data: { ok: true }, error: null }));
    const api = new DesktopApiClient({ baseUrl: "http://local", fetchImpl });
    await expect(api.request("/health", { auth: false })).resolves.toEqual({ ok: true });
  });

  it("parses an error envelope", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ success: false, data: null, error: { code: "forbidden", message: "No access" } }, { status: 403 })
    );
    const api = new DesktopApiClient({ baseUrl: "http://local", fetchImpl });
    await expect(api.request("/cameras")).rejects.toMatchObject({ code: "forbidden", message: "No access", status: 403 });
  });

  it("handles network failure", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("failed"));
    const api = new DesktopApiClient({ baseUrl: "http://local", fetchImpl });
    await expect(api.request("/health", { auth: false })).rejects.toMatchObject({ code: "network_error" });
  });

  it("attaches protected auth headers", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ success: true, data: [], error: null }));
    const api = new DesktopApiClient({
      baseUrl: "http://local",
      fetchImpl,
      getSession: () => ({ accessToken: "safe-test-access", sessionToken: "safe-test-session" })
    });
    await api.request("/cameras");
    const headers = fetchImpl.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer safe-test-access");
    expect(headers.get("X-Session-Token")).toBe("safe-test-session");
  });

  it("calls unauthorized callback on 401", async () => {
    const onUnauthorized = vi.fn();
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ success: false, data: null, error: { code: "http_401", message: "Expired" } }, { status: 401 })
    );
    const api = new DesktopApiClient({ baseUrl: "http://local", fetchImpl, onUnauthorized });
    await expect(api.request("/cameras")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });
});
