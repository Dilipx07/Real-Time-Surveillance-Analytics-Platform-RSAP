import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.security import session_identifier
from app.services.session_service import (
    activate_session, process_session_outbox_once, refresh_state, rotate_refresh,
    reconciliation_expiries, session_state, validate_session_state,
)


class Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def set(self, key, value):
        self.operations.append(("set", key, value))

    def expireat(self, key, expiry):
        self.operations.append(("expireat", key, expiry))

    async def execute(self):
        for operation, key, value in self.operations:
            if operation == "set":
                self.redis.values[key] = value
            else:
                self.redis.ttls[key] = value
        return [True] * len(self.operations)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def pipeline(self, transaction=True):
        assert transaction
        return Pipeline(self)

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, script, key_count, refresh_key, session_key, *args):
        current = json.loads(self.values[refresh_key])
        session = json.loads(self.values[session_key])
        expected_hash, expected_jti, sid, replacement = args
        if current["jti"] != expected_jti or current["sid"] != sid or current["token_hash"] != expected_hash:
            return 0
        if session["sid"] != sid:
            return 0
        self.values[refresh_key] = replacement
        self.ttls[refresh_key] = json.loads(replacement)["refresh_expires_at"]
        return 1


class NeverDB:
    async def fetchrow(self, *_):
        raise AssertionError("invalid live state must fail before PostgreSQL lookup")


@pytest.mark.asyncio
async def test_refresh_rotation_is_one_time_and_license_bounded():
    redis = FakeRedis()
    user_id = uuid4()
    token = "session-token"
    now = datetime.now(UTC)
    absolute = int((now + timedelta(hours=1)).timestamp())
    state = session_state(
        token, user_id, uuid4(), now - timedelta(minutes=1),
        now + timedelta(hours=1), absolute, absolute,
    )
    old = refresh_state("old-refresh", "old-jti", state["sid"], absolute)
    await activate_session(redis, user_id, state, old, absolute, absolute)
    replacement_expiry = absolute - 900
    replacement = refresh_state(
        "new-refresh", "new-jti", state["sid"], replacement_expiry
    )
    assert await rotate_refresh(
        redis, user_id, "old-refresh", "old-jti", state["sid"], replacement
    )
    assert not await rotate_refresh(
        redis, user_id, "old-refresh", "old-jti", state["sid"], replacement
    )
    assert redis.ttls[f"refresh:{user_id}"] == replacement_expiry


@pytest.mark.asyncio
async def test_session_rejects_jwt_session_mixing_and_invalid_license_windows():
    redis = FakeRedis()
    user_id = uuid4()
    now = datetime.now(UTC)
    absolute = int((now + timedelta(hours=1)).timestamp())
    state = session_state(
        "presented", user_id, uuid4(), now - timedelta(minutes=1),
        now + timedelta(minutes=5), absolute, absolute,
    )
    redis.values[f"session:{user_id}"] = json.dumps(state)
    assert await validate_session_state(
        redis, NeverDB(), user_id, "presented", session_identifier("different")
    ) is None
    future = session_state(
        "presented", user_id, uuid4(), now + timedelta(minutes=1),
        now + timedelta(minutes=5), absolute, absolute,
    )
    redis.values[f"session:{user_id}"] = json.dumps(future)
    assert await validate_session_state(redis, NeverDB(), user_id, "presented") is None
    expired = session_state(
        "presented", user_id, uuid4(), now - timedelta(minutes=5),
        now - timedelta(seconds=1), absolute, absolute,
    )
    redis.values[f"session:{user_id}"] = json.dumps(expired)
    assert await validate_session_state(redis, NeverDB(), user_id, "presented") is None


def test_reconciliation_shortens_and_extends_each_key_independently():
    now = 1_800_000_000
    state = {"sid": "pair", "session_expires_at": now + 600}
    refresh = {"sid": "pair", "refresh_expires_at": now + 900}

    shortened = reconciliation_expiries(state, refresh, now - 1, now + 30, now)
    extended = reconciliation_expiries(state, refresh, now - 1, now + 1200, now)

    assert shortened == (now + 30, now + 30)
    assert extended == (now + 600, now + 900)
    assert extended[0] <= state["session_expires_at"]
    assert extended[1] <= refresh["refresh_expires_at"]
    assert all(expiry <= now + 1200 for expiry in extended)


def test_reconciliation_rejects_future_license_and_incomplete_or_mismatched_pair():
    now = 1_800_000_000
    state = {"sid": "pair", "session_expires_at": now + 600}
    refresh = {"sid": "pair", "refresh_expires_at": now + 900}

    with pytest.raises(ValueError, match="not currently active"):
        reconciliation_expiries(state, refresh, now + 1, now + 1200, now)
    with pytest.raises(ValueError, match="absolute session expiry metadata"):
        reconciliation_expiries(state, {"sid": "pair"}, now - 1, now + 1200, now)
    with pytest.raises(ValueError, match="do not form a pair"):
        reconciliation_expiries(
            state, {**refresh, "sid": "different"}, now - 1, now + 1200, now
        )


@pytest.mark.asyncio
async def test_redis_revocation_failure_stays_in_durable_outbox():
    user_id = uuid4()

    class DB:
        def __init__(self):
            self.updates = []

        async def fetch(self, *_args):
            return [{"id": 9, "user_id": user_id, "operation": "revoke", "payload": {}}]

        async def execute(self, query, *args):
            self.updates.append((query, args))

    class Acquire:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    class RedisFailure:
        async def delete(self, *_args):
            raise ConnectionError("Redis unavailable")

    db = DB()
    app = SimpleNamespace(
        state=SimpleNamespace(
            db_pool=SimpleNamespace(acquire=lambda: Acquire()), redis=RedisFailure()
        )
    )
    assert await process_session_outbox_once(app) == 0
    assert any("last_error=$2" in query for query, _ in db.updates)
    assert not any("processed_at=NOW()" in query for query, _ in db.updates)
