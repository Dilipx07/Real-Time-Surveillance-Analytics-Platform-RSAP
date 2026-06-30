import asyncio
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from redis.asyncio import Redis

from app.security import session_identifier, token_hash


ROTATE_REFRESH_LUA = """
local current = redis.call('GET', KEYS[1])
local session = redis.call('GET', KEYS[2])
if not current or not session then return 0 end
local refresh_data = cjson.decode(current)
local session_data = cjson.decode(session)
if refresh_data['token_hash'] ~= ARGV[1]
   or refresh_data['jti'] ~= ARGV[2]
   or refresh_data['sid'] ~= ARGV[3]
   or session_data['sid'] ~= ARGV[3] then
  return 0
end
redis.call('SET', KEYS[1], ARGV[4], 'EX', ARGV[5])
return 1
"""

DELETE_IF_SID_LUA = """
local session = redis.call('GET', KEYS[1])
if not session then
  redis.call('DEL', KEYS[2])
  return 1
end
local data = cjson.decode(session)
if data['sid'] ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1], KEYS[2])
return 1
"""

RECONCILE_LUA = """
local session = redis.call('GET', KEYS[1])
if not session then return 0 end
local data = cjson.decode(session)
if ARGV[1] ~= '' and data['sid'] ~= ARGV[1] then return 0 end
data['license_id'] = ARGV[2]
data['license_valid_from'] = ARGV[3]
data['license_expires_at'] = ARGV[4]
redis.call('SET', KEYS[1], cjson.encode(data), 'EX', ARGV[5])
if redis.call('EXISTS', KEYS[2]) == 1 then
  local refresh_ttl = redis.call('TTL', KEYS[2])
  if refresh_ttl < 0 or refresh_ttl > tonumber(ARGV[5]) then
    redis.call('EXPIRE', KEYS[2], ARGV[5])
  end
end
return 1
"""


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def session_state(
    session_token: str,
    license_id: UUID,
    valid_from: datetime,
    valid_until: datetime,
) -> dict[str, Any]:
    sid = session_identifier(session_token)
    return {
        "sid": sid,
        "token_hash": token_hash(session_token),
        "license_id": str(license_id),
        "license_valid_from": valid_from.astimezone(UTC).isoformat(),
        "license_expires_at": valid_until.astimezone(UTC).isoformat(),
    }


def refresh_state(refresh_token: str, refresh_jti: str, sid: str) -> dict[str, str]:
    return {"token_hash": token_hash(refresh_token), "jti": refresh_jti, "sid": sid}


async def activate_session(
    redis: Redis,
    user_id: UUID,
    state: dict[str, Any],
    refresh: dict[str, str],
    session_ttl: int,
    refresh_ttl: int,
) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(f"session:{user_id}", _json(state), ex=session_ttl)
        pipe.set(f"refresh:{user_id}", _json(refresh), ex=refresh_ttl)
        results = await pipe.execute()
    if results != [True, True]:
        raise RuntimeError("Redis did not activate a coherent session pair")


async def delete_if_sid(redis: Redis, user_id: UUID, sid: str) -> bool:
    result = await redis.eval(
        DELETE_IF_SID_LUA, 2, f"session:{user_id}", f"refresh:{user_id}", sid
    )
    return bool(result)


async def load_json(redis: Redis, key: str) -> dict[str, Any] | None:
    value = await redis.get(key)
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


async def validate_session_state(
    redis: Redis,
    db: asyncpg.Connection,
    user_id: UUID,
    session_token: str,
    expected_sid: str | None = None,
) -> tuple[dict[str, Any], asyncpg.Record, list[dict]] | None:
    state = await load_json(redis, f"session:{user_id}")
    if state is None:
        return None
    sid = session_identifier(session_token)
    if (
        not hmac.compare_digest(str(state.get("token_hash", "")), token_hash(session_token))
        or not hmac.compare_digest(str(state.get("sid", "")), sid)
        or (expected_sid is not None and not hmac.compare_digest(expected_sid, sid))
    ):
        return None
    try:
        license_id = UUID(str(state["license_id"]))
        valid_from = datetime.fromisoformat(str(state["license_valid_from"])).astimezone(UTC)
        expires_at = datetime.fromisoformat(str(state["license_expires_at"])).astimezone(UTC)
    except (KeyError, ValueError, TypeError):
        return None
    now = datetime.now(UTC)
    if not valid_from <= now < expires_at:
        return None
    row = await db.fetchrow(
        """SELECT u.id, u.email, u.role
           FROM auth.users u
           JOIN auth.sessions s ON s.user_id=u.id
           JOIN rbac.licenses l ON l.id=$3 AND l.user_id=u.id
           WHERE u.id=$1 AND s.session_token=$2 AND s.revoked_at IS NULL
             AND s.expires_at > NOW() AND u.is_active=true AND u.is_deleted=false
             AND l.is_active=true AND l.valid_from <= NOW() AND l.valid_until > NOW()""",
        user_id,
        session_token,
        license_id,
    )
    if row is None:
        return None
    permissions = [
        dict(item)
        for item in await db.fetch(
            "SELECT id, resource, actions, constraints FROM rbac.permissions WHERE user_id=$1 ORDER BY created_at",
            user_id,
        )
    ]
    return state, row, permissions


async def rotate_refresh(
    redis: Redis,
    user_id: UUID,
    expected_token: str,
    expected_jti: str,
    sid: str,
    replacement: dict[str, str],
    ttl: int,
) -> bool:
    result = await redis.eval(
        ROTATE_REFRESH_LUA,
        2,
        f"refresh:{user_id}",
        f"session:{user_id}",
        token_hash(expected_token),
        expected_jti,
        sid,
        _json(replacement),
        ttl,
    )
    return result == 1


async def enqueue_session_action(
    db: asyncpg.Connection,
    user_id: UUID,
    operation: str,
    payload: dict[str, Any] | None = None,
) -> int:
    return await db.fetchval(
        """INSERT INTO auth.session_outbox(user_id, operation, payload)
           VALUES($1, $2, $3::jsonb) RETURNING id""",
        user_id,
        operation,
        payload or {},
    )


async def process_session_outbox_once(app: Any, limit: int = 100) -> int:
    processed = 0
    async with app.state.db_pool.acquire() as db:
        rows = await db.fetch(
            """SELECT id, user_id, operation, payload FROM auth.session_outbox
               WHERE processed_at IS NULL ORDER BY id LIMIT $1""",
            limit,
        )
        for row in rows:
            try:
                if row["operation"] == "revoke":
                    sid = str(row["payload"].get("sid", ""))
                    if sid:
                        await delete_if_sid(app.state.redis, row["user_id"], sid)
                    else:
                        await app.state.redis.delete(
                            f"session:{row['user_id']}", f"refresh:{row['user_id']}"
                        )
                elif row["operation"] == "reconcile":
                    license_row = await db.fetchrow(
                        """SELECT id, valid_from, valid_until FROM rbac.licenses
                           WHERE user_id=$1 AND is_active=true AND valid_from <= NOW()
                             AND valid_until > NOW() ORDER BY valid_until DESC LIMIT 1""",
                        row["user_id"],
                    )
                    if license_row is None:
                        await db.execute(
                            "UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL",
                            row["user_id"],
                        )
                        await app.state.redis.delete(
                            f"session:{row['user_id']}", f"refresh:{row['user_id']}"
                        )
                    else:
                        await db.execute(
                            """UPDATE auth.sessions SET expires_at=$2
                               WHERE user_id=$1 AND revoked_at IS NULL""",
                            row["user_id"],
                            license_row["valid_until"],
                        )
                        ttl = max(
                            1,
                            int(
                                (license_row["valid_until"] - datetime.now(UTC)).total_seconds()
                            ),
                        )
                        await app.state.redis.eval(
                            RECONCILE_LUA,
                            2,
                            f"session:{row['user_id']}",
                            f"refresh:{row['user_id']}",
                            str(row["payload"].get("sid", "")),
                            str(license_row["id"]),
                            license_row["valid_from"].astimezone(UTC).isoformat(),
                            license_row["valid_until"].astimezone(UTC).isoformat(),
                            ttl,
                        )
                await db.execute(
                    "UPDATE auth.session_outbox SET processed_at=NOW(), attempts=attempts+1, last_error=NULL WHERE id=$1",
                    row["id"],
                )
                processed += 1
            except Exception as exc:
                await db.execute(
                    "UPDATE auth.session_outbox SET attempts=attempts+1, last_error=$2 WHERE id=$1",
                    row["id"],
                    str(exc)[:2000],
                )
    return processed


async def session_outbox_worker(app: Any, stop: asyncio.Event) -> None:
    interval = app.state.settings.session_outbox_interval_seconds
    while not stop.is_set():
        try:
            await process_session_outbox_once(app)
        except Exception:
            app.state.logger.exception("Session outbox processing failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
