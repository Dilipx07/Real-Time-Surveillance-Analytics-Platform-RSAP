from collections.abc import AsyncIterator
import json

import asyncpg
from fastapi import Request

from app.config import Settings


async def create_pool(settings: Settings) -> asyncpg.Pool:
    async def configure(connection: asyncpg.Connection) -> None:
        for type_name in ("json", "jsonb"):
            await connection.set_type_codec(
                type_name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
            )

    return await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=1,
        max_size=20,
        command_timeout=30,
        server_settings={"timezone": "UTC", "application_name": "rsap-webapp-backend"},
        init=configure,
    )


async def get_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async with request.app.state.db_pool.acquire() as connection:
        yield connection
