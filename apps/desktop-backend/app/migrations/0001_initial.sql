CREATE TABLE local_sessions (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    encrypted_payload TEXT NOT NULL,
    user_id TEXT NOT NULL,
    license_valid_until TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE local_cameras (
    id TEXT PRIMARY KEY,
    server_id TEXT UNIQUE,
    name TEXT NOT NULL,
    stream_url_encrypted TEXT NOT NULL,
    stream_type TEXT NOT NULL CHECK (stream_type IN ('rtsp', 'webcam', 'nvr')),
    location_label TEXT,
    analytics_config_json TEXT NOT NULL DEFAULT '{}',
    zones_json TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    sync_state TEXT NOT NULL DEFAULT 'pending' CHECK (sync_state IN ('pending', 'synced', 'conflict')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE local_analytics_events (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES local_cameras(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    captured_image_path TEXT,
    synced INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE local_alerts (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES local_cameras(id) ON DELETE CASCADE,
    zone_id TEXT,
    image_path TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    synced INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE local_persons (
    id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone_encrypted TEXT NOT NULL,
    face_encoding_path TEXT,
    synced_at TEXT NOT NULL
);

CREATE TABLE local_people_counts (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL REFERENCES local_cameras(id) ON DELETE CASCADE,
    count_in INTEGER NOT NULL CHECK (count_in >= 0),
    count_out INTEGER NOT NULL CHECK (count_out >= 0),
    captured_at TEXT NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0, 1))
);

CREATE TABLE sync_queue (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'inflight')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    last_attempted_at TEXT,
    lease_expires_at TEXT,
    claim_token TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE local_runtime_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_events_unsynced ON local_analytics_events(synced, created_at);
CREATE INDEX idx_alerts_unsynced ON local_alerts(synced, created_at);
CREATE INDEX idx_counts_unsynced ON local_people_counts(synced, captured_at);
CREATE INDEX idx_sync_ready ON sync_queue(state, next_attempt_at, lease_expires_at);
