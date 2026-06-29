from typing import Any
from uuid import UUID

import asyncpg
from fastapi import Request

from app.models.user import CurrentUser


async def write_audit_log(
    db: asyncpg.Connection,
    request: Request,
    user: CurrentUser | UUID | None,
    action: str,
    resource: str,
    resource_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    client_ip = request.client.host if request.client else None
    await db.execute(
        """INSERT INTO audit.logs(user_id, action, resource, resource_id, metadata, ip_address)
           VALUES($1, $2, $3, $4, $5::jsonb, $6)""",
        user.id if isinstance(user, CurrentUser) else user,
        action,
        resource,
        resource_id,
        metadata or {},
        client_ip,
    )
