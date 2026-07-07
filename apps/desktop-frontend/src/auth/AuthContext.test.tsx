import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";

function Harness() {
  const { login, logout, session } = useAuth();
  return (
    <div>
      <span>{session?.user.email ?? "signed-out"}</span>
      <button type="button" onClick={() => void login("operator@example.test", "not-rendered")}>
        login
      </button>
      <button type="button" onClick={() => void logout()}>
        logout
      </button>
    </div>
  );
}

function renderHarness() {
  const client = new QueryClient();
  return render(
    <AuthProvider>
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>
    </AuthProvider>
  );
}

describe("AuthProvider", () => {
  it("stores session on login and clears it on logout", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: true,
          data: {
            access_token: "safe-access",
            session_token: "safe-session",
            token_type: "bearer",
            access_expires_at: "2099-01-01T00:00:00Z",
            user: { email: "operator@example.test" },
            license: { is_active: true }
          },
          error: null
        })
      )
    );
    vi.stubGlobal("fetch", fetchMock);
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "login" }));
    expect(await screen.findByText("operator@example.test")).toBeInTheDocument();
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ success: true, data: { logged_out: true, revocation_pending: false }, error: null }))
    );
    await userEvent.click(screen.getByRole("button", { name: "logout" }));
    await waitFor(() => expect(screen.getByText("signed-out")).toBeInTheDocument());
  });
});
