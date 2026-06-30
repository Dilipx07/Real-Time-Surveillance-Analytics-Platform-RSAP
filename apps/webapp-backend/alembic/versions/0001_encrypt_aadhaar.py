"""Upgrade legacy Aadhaar storage to support authenticated encryption.

Fresh databases are provisioned by ``infra/postgres/init.sql``, where the
``aadhaar_last4`` column is already ``TEXT``. This migration upgrades legacy
databases that may still define the column as ``CHAR`` or ``VARCHAR``.
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_encrypt_aadhaar"
# This is the first Alembic revision.  init.sql is provisioning, not a second
# Alembic revision, so there is no migration revision to reference here.
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_schema("events"):
        return
    if not inspector.has_table("registered_persons", schema="events"):
        return

    column = next(
        (
            candidate
            for candidate in inspector.get_columns("registered_persons", schema="events")
            if candidate["name"] == "aadhaar_last4"
        ),
        None,
    )
    if column is None:
        return

    current_type = column["type"]
    if isinstance(current_type, sa.Text):
        return
    if isinstance(current_type, (sa.CHAR, sa.VARCHAR)):
        op.alter_column(
            "registered_persons",
            "aadhaar_last4",
            schema="events",
            existing_type=current_type,
            type_=sa.Text(),
            existing_nullable=column.get("nullable", False),
        )


def downgrade() -> None:
    raise RuntimeError("Encrypted Aadhaar values cannot be safely converted back to CHAR(4)")
