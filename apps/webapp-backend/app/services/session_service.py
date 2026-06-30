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
local refresh_ok, refresh_data = pcall(cjson.decode, current)
local session_ok, session_data = pcall(cjson.decode, session)
local replacement_ok, replacement = pcall(cjson.decode, ARGV[4])
if not refresh_ok or not session_ok or not replacement_ok then
  redis.call('DEL', KEYS[1], KEYS[2])
  return 0
end
if refresh_data['token_hash'] ~= ARGV[1]
   or refresh_data['jti'] ~= ARGV[2]
   or refresh_data['sid'] ~= ARGV[3]
   or session_data['sid'] ~= ARGV[3]
   or replacement['sid'] ~= ARGV[3]
   or type(replacement['refresh_expires_at']) ~= 'number'
   or type(session_data['session_expires_at']) ~= 'number'
   or type(session_data['license_expires_at']) ~= 'number' then
  return 0
end
local now = tonumber(redis.call('TIME')[1])
local refresh_expiry = math.min(
  replacement['refresh_expires_at'], session_data['license_expires_at']
)
local session_expiry = math.min(
  session_data['session_expires_at'], session_data['license_expires_at']
)
if refresh_expiry <= now or session_expiry <= now then return 0 end
session_data['refresh_expires_at'] = replacement['refresh_expires_at']
redis.call('SET', KEYS[2], cjson.encode(session_data))
redis.call('EXPIREAT', KEYS[2], session_expiry)
redis.call('SET', KEYS[1], ARGV[4])
redis.call('EXPIREAT', KEYS[1], refresh_expiry)
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
local refresh = redis.call('GET', KEYS[2])
if not session or not refresh then
  redis.call('DEL', KEYS[1], KEYS[2])
  return 0
end
local session_ok, data = pcall(cjson.decode, session)
local refresh_ok, refresh_data = pcall(cjson.decode, refresh)
if not session_ok or not refresh_ok
   or not data['sid'] or data['sid'] ~= refresh_data['sid']
   or (ARGV[1] ~= '' and data['sid'] ~= ARGV[1])
   or type(data['session_expires_at']) ~= 'number'
   or type(refresh_data['refresh_expires_at']) ~= 'number' then
  redis.call('DEL', KEYS[1], KEYS[2])
  return 0
end
local now = tonumber(redis.call('TIME')[1])
local license_valid_from = tonumber(ARGV[3])
local license_expiry = tonumber(ARGV[4])
if not license_valid_from or not license_expiry
   or license_valid_from > now or license_expiry <= now then return 0 end
local session_expiry = math.min(data['session_expires_at'], license_expiry)
local refresh_expiry = math.min(refresh_data['refresh_expires_at'], license_expiry)
if session_expiry <= now or refresh_expiry <= now then
  redis.call('DEL', KEYS[1], KEYS[2])
  return 0
end
data['license_id'] = ARGV[2]
data['license_valid_from'] = license_valid_from
data['license_expires_at'] = license_expiry
data['refresh_expires_at'] = refresh_data['refresh_expires_at']
redis.call('SET', KEYS[1], cjson.encode(data))
redis.call('EXPIREAT', KEYS[1], session_expiry)
redis.call('EXPIREAT', KEYS[2], refresh_expiry)
return 1
"""


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def session_state(
    session_token: str,
    user_id: UUID,
    license_id: UUID,
    valid_from: datetime,
    valid_until: datetime,
    session_expires_at: int,
    refresh_expires_at: int,
) -> dict[str, Any]:
    sid = session_identifier(session_token)
    return {
        "sid": sid,
        "session_id": sid,
        "user_id": str(user_id),
        "token_hash": token_hash(session_token),
        "license_id": str(license_id),
        "license_valid_from": int(valid_from.astimezone(UTC).timestamp()),
        "license_expires_at": int(valid_until.astimezone(UTC).timestamp()),
        "session_expires_at": session_expires_at,
        "refresh_expires_at": refresh_expires_at,
    }


def refresh_state(
    refresh_token: str, refresh_jti: str, sid: str, refresh_expires_at: int
) -> dict[str, Any]:
    return {
        "token_hash": token_hash(refresh_token),
        "jti": refresh_jti,
        "sid": sid,
        "refresh_expires_at": refresh_expires_at,
    }


def reconciliation_expiries(
    state: dict[str, Any],
    refresh: dict[str, Any],
    license_valid_from: int,
    license_expires_at: int,
    now: int,
) -> tuple[int, int]:
    """Validate a session pair and return its independently bounded expiries."""
    if license_valid_from > now or license_expires_at <= now:
        raise ValueError("license is not currently active")
    if not state.get("sid") or state.get("sid") != refresh.get("sid"):
        raise ValueError("session and refresh metadata do not form a pair")
    try:
        session_absolute = int(state["session_expires_at"])
        refresh_absolute = int(refresh["refresh_expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("absolute session expiry metadata is required") from exc
    session_effective = min(session_absolute, license_expires_at)
    refresh_effective = min(refresh_absolute, license_expires_at)
    if session_effective <= now or refresh_effective <= now:
        raise ValueError("session pair is already expired")
    return session_effective, refresh_effective


async def activate_session(
    redis: Redis,
    user_id: UUID,
    state: dict[str, Any],
    refresh: dict[str, Any],
    session_expires_at: int,
    refresh_expires_at: int,
) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(f"session:{user_id}", _json(state))
        pipe.expireat(f"session:{user_id}", session_expires_at)
        pipe.set(f"refresh:{user_id}", _json(refresh))
        pipe.expireat(f"refresh:{user_id}", refresh_expires_at)
        results = await pipe.execute()
    if results != [True, True, True, True]:
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
        valid_from = datetime.fromtimestamp(int(state["license_valid_from"]), UTC)
        expires_at = datetime.fromtimestamp(int(state["license_expires_at"]), UTC)
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
    replacement: dict[str, Any],
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
                        await app.state.redis.eval(
                            RECONCILE_LUA,
                            2,
                            f"session:{row['user_id']}",
                            f"refresh:{row['user_id']}",
                            str(row["payload"].get("sid", "")),
                            str(license_row["id"]),
                            int(license_row["valid_from"].astimezone(UTC).timestamp()),
                            int(license_row["valid_until"].astimezone(UTC).timestamp()),
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
