CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS rbac;
CREATE SCHEMA IF NOT EXISTS events;
CREATE SCHEMA IF NOT EXISTS va;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE auth.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(20),
    password_hash TEXT NOT NULL,
    role VARCHAR(50) NOT NULL CHECK (role IN ('super_admin','admin','staff','va_user')),
    is_active BOOLEAN DEFAULT true,
    is_deleted BOOLEAN DEFAULT false,
    whatsapp_number VARCHAR(20),
    created_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Durable audit mirror. Redis remains the live session authority.
CREATE TABLE auth.sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    session_token TEXT NOT NULL UNIQUE,
    device_fingerprint TEXT,
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

CREATE TABLE rbac.licenses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    license_key TEXT UNIQUE NOT NULL,
    features JSONB NOT NULL DEFAULT '{}',
    max_cameras INTEGER DEFAULT 8,
    analytics_modules JSONB DEFAULT '[]',
    valid_from TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE rbac.permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    resource VARCHAR(100) NOT NULL,
    actions TEXT[] NOT NULL,
    constraints JSONB DEFAULT '{}',
    granted_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE events.registered_persons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID,
    full_name VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    aadhaar_last4 TEXT NOT NULL,
    face_encoding BYTEA,
    face_image_id UUID,
    registered_by UUID REFERENCES auth.users(id),
    entry_status VARCHAR(20) DEFAULT 'not_entered',
    entry_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE va.cameras (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    name VARCHAR(255) NOT NULL,
    stream_url_encrypted TEXT NOT NULL,
    stream_type VARCHAR(20) CHECK (stream_type IN ('rtsp','webcam','nvr')),
    location_label VARCHAR(255),
    analytics_config JSONB DEFAULT '{}',
    zones JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE va.analytics_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id UUID REFERENCES va.cameras(id),
    event_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    captured_image_id UUID,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE va.intrusion_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id UUID REFERENCES va.cameras(id),
    zone_id TEXT,
    captured_image_id UUID,
    confidence FLOAT,
    resolved BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit.logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    action VARCHAR(255) NOT NULL,
    resource VARCHAR(255),
    resource_id UUID,
    metadata JSONB DEFAULT '{}',
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sessions_user ON auth.sessions(user_id);
CREATE INDEX idx_sessions_token ON auth.sessions(session_token);
CREATE INDEX idx_licenses_user ON rbac.licenses(user_id);
CREATE INDEX idx_analytics_camera ON va.analytics_events(camera_id);
CREATE INDEX idx_analytics_created ON va.analytics_events(created_at DESC);
CREATE INDEX idx_persons_event ON events.registered_persons(event_id);
