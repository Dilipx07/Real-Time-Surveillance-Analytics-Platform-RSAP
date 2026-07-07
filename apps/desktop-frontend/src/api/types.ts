export type ApiErrorCode =
  | "http_400"
  | "http_401"
  | "http_403"
  | "http_404"
  | "http_409"
  | "http_422"
  | "http_500"
  | "network_error"
  | "timeout"
  | "malformed_response"
  | "unknown_error"
  | string;

export interface ApiErrorShape {
  code: ApiErrorCode;
  message: string;
  status?: number;
}

export interface ApiEnvelope<T> {
  success: boolean;
  data: T | null;
  error: ApiErrorShape | string | null;
}

export interface Paginated<T> {
  items: T[];
  limit: number;
  offset: number;
  total: number;
}

export interface UserProfile {
  id?: string;
  email?: string;
  role?: string;
  permissions?: string[];
  [key: string]: unknown;
}

export interface LicenseInfo {
  valid_until?: string;
  is_active?: boolean;
  features?: Record<string, unknown>;
  max_cameras?: number;
  analytics_modules?: string[];
  [key: string]: unknown;
}

export interface LocalSession {
  access_token: string;
  session_token: string;
  refresh_token?: string | null;
  token_type: "bearer";
  access_expires_at: string;
  user: UserProfile;
  license?: LicenseInfo | null;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface CameraDto {
  id: string;
  server_id: string | null;
  name: string;
  stream_url?: string;
  stream_type: "rtsp" | "webcam" | "nvr";
  location_label: string | null;
  analytics_config: Record<string, unknown>;
  zones: Array<Record<string, unknown>>;
  is_active: boolean;
  sync_state: string;
  created_at: string;
  updated_at: string;
}

export interface CameraCreateRequest {
  name: string;
  stream_url: string;
  stream_type: "rtsp" | "webcam" | "nvr";
  location_label?: string | null;
  analytics_config?: Record<string, unknown>;
  zones?: Array<Record<string, unknown>>;
}

export type CameraUpdateRequest = Partial<CameraCreateRequest> & {
  is_active?: boolean;
};

export type CameraHealth = "ok" | "degraded" | "failed";
export type LifecycleState =
  | "stopped"
  | "starting"
  | "running"
  | "reconnecting"
  | "stopping"
  | "failed";

export interface RuntimeServiceStatus {
  running: boolean;
  desired_running: boolean;
  reconciliation_active: boolean;
  scheduler_registered: boolean;
  last_reconciliation_at: string | null;
  last_error: string | null;
  [key: string]: unknown;
}

export interface OrchestrationCameraStatus {
  camera_id: string;
  generation: number;
  lifecycle_state: LifecycleState;
  health: CameraHealth;
  is_running: boolean;
  updated_at: string;
  last_frame_at: string | null;
  last_event_at: string | null;
  last_processed_at: string | null;
  failure_category: string | null;
  error_summary: string | null;
  reconnect_count: number;
  processing_fps: number;
  frame_buffer_size: number;
  frame_buffer_capacity: number;
  event_queue_size: number;
  event_queue_capacity: number;
  callback_backlog: number;
  dropped_event_count: number;
  frames_captured: number;
  frames_processed: number;
  capture_failures: number;
  events_emitted: number;
  event_sink_failures: number;
  worker_failures: number;
  transition_count: number;
}

export interface OrchestrationHealth {
  service: RuntimeServiceStatus;
  status: CameraHealth;
  active_cameras: number;
  failed_cameras: number;
  cameras: OrchestrationCameraStatus[];
}

export interface LifecycleOperationResult {
  camera_id: string;
  operation: "start" | "stop" | "restart";
  outcome:
    | "started"
    | "already_running"
    | "stopped"
    | "already_stopped"
    | "restarted"
    | "failed";
  generation: number | null;
  state: LifecycleState;
  status: OrchestrationCameraStatus | null;
  error: { category?: string; message?: string } | null;
}

export interface AnalyticsEventDto {
  id: string;
  camera_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  captured_image_path: string | null;
  captured_image_id: string | null;
  synced: boolean;
  created_at: string;
}

export interface PersonDto {
  id: string;
  server_id: string;
  name: string;
  phone: string;
  face_encoding_path: string | null;
  synced_at: string;
}

export interface SyncStatus {
  connected: boolean;
  queue_count: number;
  dead_letter_count: number;
  last_checked_at: string | null;
  last_error: string | null;
}

export interface DeadLetterDto {
  id: string;
  logical_key: string;
  endpoint: string;
  attempt_count: number;
  max_attempts: number;
  last_error_code: string | null;
  last_error_message: string | null;
  failure_class: string | null;
  failed_at: string | null;
  created_at: string;
}

export interface BackendHealth {
  status: string;
  database: string;
  sync: "connected" | "offline" | string;
  orchestration: OrchestrationHealth;
  timestamp: string;
}
