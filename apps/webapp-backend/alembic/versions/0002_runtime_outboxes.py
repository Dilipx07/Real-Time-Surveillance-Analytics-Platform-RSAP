"""Add durable session and external-cleanup outboxes."""

from alembic import op

revision = "0002_runtime_outboxes"
down_revision = "0001_encrypt_aadhaar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS auth.session_outbox (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES auth.users(id),
            operation VARCHAR(20) NOT NULL CHECK (operation IN ('revoke','reconcile')),
            payload JSONB NOT NULL DEFAULT '{}',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_outbox_pending ON auth.session_outbox(id) WHERE processed_at IS NULL"
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS audit.external_cleanup_outbox (
            id BIGSERIAL PRIMARY KEY,
            bucket VARCHAR(255) NOT NULL,
            object_name TEXT NOT NULL,
            operation VARCHAR(20) NOT NULL DEFAULT 'delete' CHECK (operation = 'delete'),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ,
            UNIQUE(bucket, object_name, operation)
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_cleanup_pending ON audit.external_cleanup_outbox(id) WHERE processed_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit.external_cleanup_outbox")
    op.execute("DROP TABLE IF EXISTS auth.session_outbox")
