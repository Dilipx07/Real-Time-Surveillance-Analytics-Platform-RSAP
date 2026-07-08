export type Envelope<T> = {
  success: boolean;
  data: T;
  error: string | null;
};

export type Paginated<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
};

export type AuthSession = {
  access_token: string;
  refresh_token: string;
  session_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
};

export type User = {
  id: string;
  email: string;
  role: string;
  is_active?: boolean;
  phone?: string | null;
  whatsapp_number?: string | null;
  created_at?: string;
};

export type License = {
  id: string;
  user_id: string;
  valid_from: string;
  valid_until: string;
  is_active: boolean;
  max_cameras: number;
  features: Record<string, boolean>;
  analytics_modules: string[];
};

export type Camera = {
  id: string;
  name: string;
  stream_url: string;
  stream_type: string;
  location_label?: string | null;
  analytics_config: Record<string, unknown>;
  zones: Record<string, unknown>[];
  is_active: boolean;
};

export type Person = {
  id: string;
  full_name: string;
  phone: string;
  aadhaar_last4: string;
  entry_status: string;
  created_at: string;
};

export type Dashboard = {
  total_persons: number;
  today_entries: number;
  active_cameras: number;
  open_alerts: number;
};

export type AnalyticsEvent = {
  id: string;
  camera_name: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type Alert = {
  id: string;
  camera_name: string;
  zone_id?: string | null;
  confidence?: number | null;
  resolved: boolean;
  created_at: string;
};

export type Permission = {
  id: string;
  resource: string;
  actions: string[];
  constraints: Record<string, unknown>;
};

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const HEALTH_BASE = API_BASE.replace(/\/api\/v1\/?$/, "");

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
  }
}

export function authHeaders(session: AuthSession | null): HeadersInit {
  if (!session) {
    return {};
  }
  return {
    Authorization: `Bearer ${session.access_token}`,
    "X-Session-Token": session.session_token
  };
}

async function parseEnvelope<T>(response: Response): Promise<T> {
  const body = (await response.json().catch(() => null)) as Envelope<T> | null;
  if (!response.ok || !body?.success) {
    throw new ApiError(body?.error ?? `Request failed with HTTP ${response.status}`, response.status);
  }
  return body.data;
}

export async function login(email: string, password: string): Promise<AuthSession> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, device_fingerprint: "rsap-webapp" })
  });
  return parseEnvelope<AuthSession>(response);
}

export async function getHealth(): Promise<Record<string, string>> {
  const response = await fetch(`${HEALTH_BASE}/health`, { cache: "no-store" });
  return parseEnvelope<Record<string, string>>(response);
}

export async function apiGet<T>(path: string, session: AuthSession): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: authHeaders(session),
    cache: "no-store"
  });
  return parseEnvelope<T>(response);
}

async function apiRequest<T>(path: string, session: AuthSession, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  return parseEnvelope<T>(response);
}

export async function apiPost<T>(path: string, session: AuthSession, body: unknown): Promise<T> {
  return apiRequest<T>(path, session, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(session) },
    body: JSON.stringify(body)
  });
}

export async function apiPatch<T>(path: string, session: AuthSession, body: unknown): Promise<T> {
  return apiRequest<T>(path, session, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(session) },
    body: JSON.stringify(body)
  });
}

export async function apiDelete<T>(path: string, session: AuthSession): Promise<T> {
  return apiRequest<T>(path, session, {
    method: "DELETE",
    headers: authHeaders(session)
  });
}

export async function apiPostForm<T>(path: string, session: AuthSession, body: FormData): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: authHeaders(session),
    body
  });
  return parseEnvelope<T>(response);
}

export { API_BASE };
