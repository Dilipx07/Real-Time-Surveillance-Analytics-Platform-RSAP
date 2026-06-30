from typing import Any
from uuid import UUID
from ipaddress import ip_address

import asyncpg
from fastapi import Request

from app.models.user import CurrentUser


def request_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    try:
        return str(ip_address(request.client.host))
    except ValueError:
        return None


async def write_audit_log(
    db: asyncpg.Connection,
    request: Request,
    user: CurrentUser | UUID | None,
    action: str,
    resource: str,
    resource_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    client_ip = request_ip(request)
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
