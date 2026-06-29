"""Allow authenticated encryption for Aadhaar last-four values."""

from alembic import op
import sqlalchemy as sa

revision = "0001_encrypt_aadhaar"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("registered_persons", "aadhaar_last4", schema="events", existing_type=sa.CHAR(4), type_=sa.Text(), existing_nullable=False)


def downgrade() -> None:
    raise RuntimeError("Encrypted Aadhaar values cannot be safely converted back to CHAR(4)")
