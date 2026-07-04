ALTER TABLE local_sessions ADD COLUMN generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE local_sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE local_sessions ADD COLUMN last_error TEXT;

ALTER TABLE local_analytics_events ADD COLUMN captured_image_id TEXT;
ALTER TABLE local_alerts ADD COLUMN captured_image_id TEXT;

ALTER TABLE sync_queue RENAME TO sync_queue_legacy;

CREATE TABLE sync_queue (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    logical_key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    predecessor_id TEXT,
    depends_on_id TEXT,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','inflight','retry_wait','succeeded','dead_letter','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 8 CHECK (max_attempts >= 1),
    next_attempt_at TEXT NOT NULL,
    last_attempt_at TEXT,
    lease_expires_at TEXT,
    claim_token TEXT,
    lease_owner TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    failure_class TEXT CHECK (failure_class IS NULL OR failure_class IN ('transient','permanent')),
    failed_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(logical_key, version)
);

INSERT INTO sync_queue(
    id, endpoint, payload_json, logical_key, version, state, attempt_count,
    next_attempt_at, last_attempt_at, lease_expires_at, claim_token, lease_owner,
    last_error_code, last_error_message, created_at
)
SELECT id, endpoint, payload_json, dedupe_key, 1,
       CASE state WHEN 'inflight' THEN 'retry_wait' ELSE 'pending' END,
       attempts, next_attempt_at, last_attempted_at, NULL, NULL, NULL,
       CASE WHEN last_error IS NULL THEN NULL ELSE 'legacy_error' END,
       last_error, created_at
FROM sync_queue_legacy;

DROP TABLE sync_queue_legacy;

CREATE INDEX idx_sync_ready ON sync_queue(state, next_attempt_at, created_at);
CREATE INDEX idx_sync_claim ON sync_queue(claim_token, lease_owner);
CREATE INDEX idx_sync_logical ON sync_queue(logical_key, version DESC);
CREATE INDEX idx_sync_dependency ON sync_queue(depends_on_id, predecessor_id);
CREATE INDEX idx_sync_retention ON sync_queue(state, completed_at, failed_at);
