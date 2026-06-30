import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.security import session_identifier
from app.services.session_service import (
    activate_session, process_session_outbox_once, refresh_state, rotate_refresh,
    session_state, validate_session_state,
)


class Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def set(self, key, value, ex):
        self.operations.append((key, value, ex))

    async def execute(self):
        for key, value, ttl in self.operations:
            self.redis.values[key] = value
            self.redis.ttls[key] = ttl
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
        expected_hash, expected_jti, sid, replacement, ttl = args
        if current != {"jti": expected_jti, "sid": sid, "token_hash": expected_hash}:
            return 0
        if session["sid"] != sid:
            return 0
        self.values[refresh_key] = replacement
        self.ttls[refresh_key] = int(ttl)
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
    state = session_state(token, uuid4(), now - timedelta(minutes=1), now + timedelta(hours=1))
    old = refresh_state("old-refresh", "old-jti", state["sid"])
    await activate_session(redis, user_id, state, old, 3600, 3600)
    replacement = refresh_state("new-refresh", "new-jti", state["sid"])
    assert await rotate_refresh(redis, user_id, "old-refresh", "old-jti", state["sid"], replacement, 900)
    assert not await rotate_refresh(redis, user_id, "old-refresh", "old-jti", state["sid"], replacement, 900)
    assert redis.ttls[f"refresh:{user_id}"] == 900


@pytest.mark.asyncio
async def test_session_rejects_jwt_session_mixing_and_invalid_license_windows():
    redis = FakeRedis()
    user_id = uuid4()
    now = datetime.now(UTC)
    state = session_state("presented", uuid4(), now - timedelta(minutes=1), now + timedelta(minutes=5))
    redis.values[f"session:{user_id}"] = json.dumps(state)
    assert await validate_session_state(
        redis, NeverDB(), user_id, "presented", session_identifier("different")
    ) is None
    future = session_state("presented", uuid4(), now + timedelta(minutes=1), now + timedelta(minutes=5))
    redis.values[f"session:{user_id}"] = json.dumps(future)
    assert await validate_session_state(redis, NeverDB(), user_id, "presented") is None
    expired = session_state("presented", uuid4(), now - timedelta(minutes=5), now - timedelta(seconds=1))
    redis.values[f"session:{user_id}"] = json.dumps(expired)
    assert await validate_session_state(redis, NeverDB(), user_id, "presented") is None


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
