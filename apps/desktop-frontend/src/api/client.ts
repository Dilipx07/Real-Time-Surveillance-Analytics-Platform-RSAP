import type {
  AnalyticsEventDto,
  ApiEnvelope,
  ApiErrorShape,
  BackendHealth,
  CameraCreateRequest,
  CameraDto,
  CameraUpdateRequest,
  DeadLetterDto,
  LifecycleOperationResult,
  LocalSession,
  LoginRequest,
  OrchestrationCameraStatus,
  OrchestrationHealth,
  Paginated,
  PersonDto,
  SyncStatus,
  UserProfile
} from "./types";

export class ApiError extends Error {
  readonly code: string;
  readonly status?: number;

  constructor(shape: ApiErrorShape) {
    super(shape.message);
    this.name = "ApiError";
    this.code = shape.code;
    this.status = shape.status;
  }
}

export interface SessionTokens {
  accessToken: string;
  sessionToken: string;
}

export interface ApiClientOptions {
  baseUrl?: string;
  timeoutMs?: number;
  getSession?: () => SessionTokens | null;
  onUnauthorized?: () => void;
  fetchImpl?: typeof fetch;
}

const DEFAULT_BASE_URL = "http://127.0.0.1:8001";

function configuredBaseUrl(): string {
  return import.meta.env.VITE_RSAP_DESKTOP_API_URL || DEFAULT_BASE_URL;
}

function normalizeError(error: ApiEnvelope<unknown>["error"], status: number): ApiErrorShape {
  if (typeof error === "string") {
    return { code: `http_${status}`, message: error || "Request failed", status };
  }
  if (error && typeof error === "object") {
    return {
      code: String(error.code || `http_${status}`),
      message: String(error.message || "Request failed"),
      status
    };
  }
  return { code: `http_${status}`, message: "Request failed", status };
}

async function parseEnvelope<T>(response: Response): Promise<T> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError({
      code: "malformed_response",
      message: "The desktop backend returned a malformed response.",
      status: response.status
    });
  }

  if (!body || typeof body !== "object" || !("success" in body)) {
    throw new ApiError({
      code: "malformed_response",
      message: "The desktop backend response envelope is invalid.",
      status: response.status
    });
  }

  const envelope = body as ApiEnvelope<T>;
  if (!response.ok || envelope.success !== true) {
    throw new ApiError(normalizeError(envelope.error, response.status));
  }
  return envelope.data as T;
}

export class DesktopApiClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly getSession: () => SessionTokens | null;
  private readonly onUnauthorized?: () => void;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl || configuredBaseUrl()).replace(/\/$/, "");
    this.timeoutMs = options.timeoutMs ?? 10000;
    this.getSession = options.getSession ?? (() => null);
    this.onUnauthorized = options.onUnauthorized;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async request<T>(
    path: string,
    options: RequestInit & { auth?: boolean; timeoutMs?: number } = {}
  ): Promise<T> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs ?? this.timeoutMs);
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (options.auth !== false) {
      const session = this.getSession();
      if (session) {
        headers.set("Authorization", `Bearer ${session.accessToken}`);
        headers.set("X-Session-Token", session.sessionToken);
      }
    }

    try {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...options,
        headers,
        signal: controller.signal
      });
      const data = await parseEnvelope<T>(response);
      return data;
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 401) {
          this.onUnauthorized?.();
        }
        throw error;
      }
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ApiError({ code: "timeout", message: "The desktop backend did not respond in time." });
      }
      throw new ApiError({
        code: "network_error",
        message: "The desktop backend is offline or unreachable."
      });
    } finally {
      window.clearTimeout(timeout);
    }
  }

  login(payload: LoginRequest): Promise<LocalSession> {
    return this.request<LocalSession>("/auth/login", {
      method: "POST",
      auth: false,
      body: JSON.stringify(payload)
    });
  }

  logout(): Promise<{ logged_out: boolean; revocation_pending: boolean }> {
    return this.request("/auth/logout", { method: "POST" });
  }

  me(): Promise<UserProfile> {
    return this.request("/auth/me");
  }

  health(): Promise<BackendHealth> {
    return this.request("/health", { auth: false, timeoutMs: 5000 });
  }

  listCameras(): Promise<Paginated<CameraDto>> {
    return this.request("/cameras");
  }

  createCamera(payload: CameraCreateRequest): Promise<CameraDto> {
    return this.request("/cameras", { method: "POST", body: JSON.stringify(payload) });
  }

  updateCamera(id: string, payload: CameraUpdateRequest): Promise<CameraDto> {
    return this.request(`/cameras/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
  }

  deleteCamera(id: string): Promise<{ deleted: boolean; id: string }> {
    return this.request(`/cameras/${id}`, { method: "DELETE" });
  }

  orchestrationHealth(): Promise<OrchestrationHealth> {
    return this.request("/orchestration/health");
  }

  orchestrationCameras(): Promise<OrchestrationCameraStatus[]> {
    return this.request("/orchestration/cameras");
  }

  cameraStatus(id: string): Promise<OrchestrationCameraStatus> {
    return this.request(`/orchestration/cameras/${id}/status`);
  }

  cameraOperation(id: string, operation: "start" | "stop" | "restart"): Promise<LifecycleOperationResult> {
    return this.request(`/orchestration/cameras/${id}/${operation}`, { method: "POST" });
  }

  listAnalyticsEvents(): Promise<Paginated<AnalyticsEventDto>> {
    return this.request("/analytics/events");
  }

  listPersons(): Promise<Paginated<PersonDto>> {
    return this.request("/persons");
  }

  syncStatus(): Promise<SyncStatus> {
    return this.request("/sync/status");
  }

  deadLetters(): Promise<Paginated<DeadLetterDto>> {
    return this.request("/sync/dead-letters");
  }

  retryDeadLetter(id: string): Promise<{ retried: boolean; id: string }> {
    return this.request(`/sync/dead-letters/${id}/retry`, { method: "POST" });
  }

  discardDeadLetter(id: string): Promise<{ discarded: boolean; id: string }> {
    return this.request(`/sync/dead-letters/${id}`, { method: "DELETE" });
  }
}
