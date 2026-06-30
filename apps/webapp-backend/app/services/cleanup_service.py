import asyncio
import logging

import asyncpg
from fastapi import FastAPI


logger = logging.getLogger("rsap.cleanup")


async def enqueue_external_cleanup(
    db: asyncpg.Connection, bucket: str, object_name: str
) -> None:
    await db.execute(
        """INSERT INTO audit.external_cleanup_outbox(bucket, object_name)
           VALUES($1, $2) ON CONFLICT(bucket, object_name, operation) DO NOTHING""",
        bucket,
        object_name,
    )


async def process_external_cleanup_once(app: FastAPI, limit: int = 25) -> int:
    processed = 0
    async with app.state.db_pool.acquire() as db:
        rows = await db.fetch(
            """SELECT id, bucket, object_name FROM audit.external_cleanup_outbox
               WHERE processed_at IS NULL ORDER BY id LIMIT $1""",
            limit,
        )
        for row in rows:
            try:
                await app.state.file_service.delete_file(row["bucket"], row["object_name"])
            except Exception as exc:
                await db.execute(
                    """UPDATE audit.external_cleanup_outbox SET attempts=attempts+1,
                       last_error=$2 WHERE id=$1 AND processed_at IS NULL""",
                    row["id"],
                    str(exc)[:2000],
                )
                logger.warning("External cleanup failed for outbox row %s", row["id"])
            else:
                await db.execute(
                    """UPDATE audit.external_cleanup_outbox SET attempts=attempts+1,
                       last_error=NULL, processed_at=NOW() WHERE id=$1""",
                    row["id"],
                )
                processed += 1
    return processed


async def external_cleanup_worker(app: FastAPI, stop: asyncio.Event) -> None:
    interval = app.state.settings.external_cleanup_interval_seconds
    while not stop.is_set():
        try:
            await process_external_cleanup_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("External cleanup outbox processing failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
