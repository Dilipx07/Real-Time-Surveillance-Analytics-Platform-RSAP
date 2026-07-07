import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { App } from "../App";

const session = {
  access_token: "safe-access",
  session_token: "safe-session",
  token_type: "bearer",
  access_expires_at: "2099-01-01T00:00:00Z",
  user: { email: "operator@example.test", role: "va_user" },
  license: { is_active: true }
};

const cameraList = {
  items: [
    {
      id: "cam-1",
      server_id: null,
      name: "Gate Camera",
      stream_type: "rtsp",
      location_label: "Gate",
      analytics_config: {},
      zones: [],
      is_active: true,
      sync_state: "pending",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z"
    }
  ],
  limit: 100,
  offset: 0,
  total: 1
};

const runtimeStatus = {
  camera_id: "cam-1",
  generation: 1,
  lifecycle_state: "stopped",
  health: "ok",
  is_running: false,
  updated_at: "2026-01-01T00:00:00Z",
  last_frame_at: null,
  last_event_at: null,
  last_processed_at: null,
  failure_category: null,
  error_summary: null,
  reconnect_count: 0,
  processing_fps: 0,
  frame_buffer_size: 0,
  frame_buffer_capacity: 10,
  event_queue_size: 0,
  event_queue_capacity: 100,
  callback_backlog: 0,
  dropped_event_count: 0,
  frames_captured: 0,
  frames_processed: 0,
  capture_failures: 0,
  events_emitted: 0,
  event_sink_failures: 0,
  worker_failures: 0,
  transition_count: 1
};

function ok(data: unknown) {
  return Promise.resolve(new Response(JSON.stringify({ success: true, data, error: null })));
}

function renderApp(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <AuthProvider>
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    </AuthProvider>
  );
}

describe("desktop screens", () => {
  it("renders camera backend data and empty state", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith("/auth/login")) return ok(session);
      if (url.endsWith("/health")) return ok({ status: "ok", database: "ok", sync: "offline", orchestration: { status: "ok", active_cameras: 0, failed_cameras: 0, cameras: [], service: {} }, timestamp: "2026-01-01T00:00:00Z" });
      if (url.endsWith("/cameras")) return ok(cameraList);
      return ok({ status: "ok", active_cameras: 0, failed_cameras: 0, cameras: [], service: {} });
    });
    renderApp(fetchMock);
    await userEvent.type(screen.getByLabelText("Email"), "operator@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "safe-password");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await userEvent.click(await screen.findByRole("button", { name: "Cameras" }));
    expect(await screen.findByText("Gate Camera")).toBeInTheDocument();
  });

  it("renders backend offline state", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith("/auth/login")) return ok(session);
      return Promise.reject(new TypeError("offline"));
    });
    renderApp(fetchMock);
    await userEvent.type(screen.getByLabelText("Email"), "operator@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "safe-password");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect((await screen.findAllByText(/offline or unreachable/i)).length).toBeGreaterThan(0);
  });

  it("renders orchestration health and disables operation buttons while pending", async () => {
    let startResolver: ((value: Response) => void) | undefined;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/auth/login")) return ok(session);
      if (url.endsWith("/health")) return ok({ status: "ok", database: "ok", sync: "connected", orchestration: { status: "ok", active_cameras: 0, failed_cameras: 0, cameras: [], service: {} }, timestamp: "2026-01-01T00:00:00Z" });
      if (url.endsWith("/orchestration/health")) return ok({ status: "ok", active_cameras: 0, failed_cameras: 0, cameras: [runtimeStatus], service: { running: true } });
      if (url.endsWith("/orchestration/cameras")) return ok([runtimeStatus]);
      if (url.endsWith("/orchestration/cameras/cam-1/start") && init?.method === "POST") {
        return new Promise<Response>((resolve) => {
          startResolver = resolve;
        });
      }
      return ok({});
    });
    renderApp(fetchMock);
    await userEvent.type(screen.getByLabelText("Email"), "operator@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "safe-password");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await userEvent.click(await screen.findByRole("button", { name: "Orchestration" }));
    const start = await screen.findByRole("button", { name: "Start" });
    await userEvent.click(start);
    expect(start).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/orchestration/cameras/cam-1/start"), expect.objectContaining({ method: "POST" }));
    startResolver?.(new Response(JSON.stringify({ success: true, data: { camera_id: "cam-1", operation: "start", outcome: "started", generation: 2, state: "running", status: { ...runtimeStatus, lifecycle_state: "running", is_running: true }, error: null }, error: null })));
    await waitFor(() => expect(screen.getByText(/outcome: started/i)).toBeInTheDocument());
  });

  it("does not render token-like session values", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith("/auth/login")) return ok(session);
      if (url.endsWith("/health")) return ok({ status: "ok", database: "ok", sync: "connected", orchestration: { status: "ok", active_cameras: 0, failed_cameras: 0, cameras: [], service: {} }, timestamp: "2026-01-01T00:00:00Z" });
      return ok({ status: "ok", active_cameras: 0, failed_cameras: 0, cameras: [], service: {} });
    });
    const { container } = renderApp(fetchMock);
    await userEvent.type(screen.getByLabelText("Email"), "operator@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "safe-password");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await screen.findByText("operator@example.test");
    expect(container).not.toHaveTextContent("safe-access");
    expect(container).not.toHaveTextContent("safe-session");
    expect(container).not.toHaveTextContent("safe-password");
  });
});
