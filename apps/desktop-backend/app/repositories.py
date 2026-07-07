"""Transaction-safe repositories for local edge state."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.crypto import FieldCipher
from app.database import Database
from app.dtos import (
    CentralAlert,
    CentralAnalyticsEvent,
    CentralCameraCreate,
    CentralCameraUpdate,
    CentralPeopleCount,
    require_central_image_id,
)
from app.schemas import (
    AlertCreate,
    AnalyticsEventCreate,
    CameraCreate,
    CameraUpdate,
    LocalSession,
    PeopleCountCreate,
    PersonCacheEntry,
    SessionRecord,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).isoformat()


def dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def load(value: str) -> Any:
    return json.loads(value)


def _enqueue(
    connection: Any,
    endpoint: str,
    payload: dict[str, Any],
    logical_key: str,
    max_attempts: int,
    depends_on_id: str | None = None,
) -> str:
    """Coalesce only unleased work; preserve an immutable successor after a lease."""
    now = iso()
    latest = connection.execute(
        "SELECT * FROM sync_queue WHERE logical_key=? ORDER BY version DESC LIMIT 1",
        (logical_key,),
    ).fetchone()
    if (
        latest is not None
        and latest["state"] == "pending"
        and latest["last_attempt_at"] is None
    ):
        connection.execute(
            """UPDATE sync_queue SET endpoint=?, payload_json=?, state='pending',
                   attempt_count=0, next_attempt_at=?, depends_on_id=?,
                   last_error_code=NULL, last_error_message=NULL,
                   failure_class=NULL, failed_at=NULL
               WHERE id=? AND state IN ('pending','retry_wait')""",
            (endpoint, dump(payload), now, depends_on_id or latest["depends_on_id"], latest["id"]),
        )
        return str(latest["id"])
    item_id = str(uuid4())
    version = int(latest["version"]) + 1 if latest is not None else 1
    predecessor = str(latest["id"]) if latest is not None else None
    connection.execute(
        """INSERT INTO sync_queue(
               id, endpoint, payload_json, logical_key, version, predecessor_id,
               depends_on_id, max_attempts, next_attempt_at, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id, endpoint, dump(payload), logical_key, version, predecessor,
            depends_on_id, max_attempts, now, now,
        ),
    )
    return item_id


class SessionRepository:
    def __init__(self, database: Database, cipher: FieldCipher) -> None:
        self.database = database
        self.cipher = cipher

    def _record(self, row: Any) -> SessionRecord:
        payload = self.cipher.decrypt_json(row["encrypted_payload"], "local-session")
        expiry = datetime.fromisoformat(row["license_valid_until"]) if row["license_valid_until"] else None
        return SessionRecord(
            session=LocalSession.model_validate(payload),
            license_valid_until=expiry,
            generation=int(row["generation"]),
            status=row["status"],
            last_error=row["last_error"],
        )

    async def save(self, session: LocalSession, license_valid_until: datetime | None) -> SessionRecord:
        """Install a newly authenticated session and advance its generation."""
        encrypted = self.cipher.encrypt_json(session.model_dump(mode="json"), "local-session")
        user_id = str(session.user.get("id", ""))
        if not user_id:
            raise ValueError("central login response did not include user.id")

        def operation(connection: Any) -> Any:
            current = connection.execute(
                "SELECT generation FROM local_sessions WHERE singleton=1"
            ).fetchone()
            generation = int(current["generation"]) + 1 if current else 1
            connection.execute(
                """INSERT INTO local_sessions(
                       singleton, encrypted_payload, user_id, license_valid_until,
                       updated_at, generation, status, last_error
                   ) VALUES (1, ?, ?, ?, ?, ?, 'active', NULL)
                   ON CONFLICT(singleton) DO UPDATE SET
                       encrypted_payload=excluded.encrypted_payload,
                       user_id=excluded.user_id,
                       license_valid_until=excluded.license_valid_until,
                       updated_at=excluded.updated_at,
                       generation=excluded.generation,
                       status='active', last_error=NULL""",
                (
                    encrypted, user_id, iso(license_valid_until) if license_valid_until else None,
                    iso(), generation,
                ),
            )
            connection.execute(
                """UPDATE sync_queue SET state='cancelled',completed_at=?
                   WHERE logical_key='session:revoke'
                     AND state IN ('pending','retry_wait','dead_letter')""",
                (iso(),),
            )
            return connection.execute("SELECT * FROM local_sessions WHERE singleton=1").fetchone()

        return self._record(await self.database.write(operation))

    async def get_record(self) -> SessionRecord | None:
        row = await self.database.read(lambda connection: connection.execute(
            "SELECT * FROM local_sessions WHERE singleton=1"
        ).fetchone())
        return self._record(row) if row else None

    async def get(self) -> tuple[LocalSession, datetime | None] | None:
        record = await self.get_record()
        return (record.session, record.license_valid_until) if record else None

    async def replace_active(
        self,
        session: LocalSession,
        license_valid_until: datetime,
        expected_generation: int,
    ) -> bool:
        encrypted = self.cipher.encrypt_json(session.model_dump(mode="json"), "local-session")

        def operation(connection: Any) -> bool:
            cursor = connection.execute(
                """UPDATE local_sessions SET encrypted_payload=?, license_valid_until=?,
                       generation=generation+1, updated_at=?, last_error=NULL
                   WHERE singleton=1 AND generation=? AND status='active'""",
                (encrypted, iso(license_valid_until), iso(), expected_generation),
            )
            return cursor.rowcount == 1

        return await self.database.write(operation)

    async def mark_revocation_pending(self, expected_generation: int) -> int | None:
        def operation(connection: Any) -> int | None:
            cursor = connection.execute(
                """UPDATE local_sessions SET status='revocation_pending',
                       generation=generation+1, updated_at=?, last_error=NULL
                   WHERE singleton=1 AND generation=? AND status='active'""",
                (iso(), expected_generation),
            )
            return expected_generation + 1 if cursor.rowcount == 1 else None

        return await self.database.write(operation)

    async def begin_revocation(
        self, expected_generation: int, max_attempts: int, lease_seconds: int
    ) -> tuple[int, str, str] | None:
        """Atomically deny authorization and create the first durable revocation lease."""
        generation = expected_generation + 1
        claim_token = str(uuid4())
        owner = "auth-logout"

        def operation(connection: Any) -> tuple[int, str, str] | None:
            cursor = connection.execute(
                """UPDATE local_sessions SET status='revocation_pending',generation=?,
                       updated_at=?,last_error=NULL
                   WHERE singleton=1 AND generation=? AND status='active'""",
                (generation, iso(), expected_generation),
            )
            if cursor.rowcount != 1:
                return None
            item_id = _enqueue(
                connection, "/api/v1/auth/logout",
                {"_kind": "session_revoke", "generation": generation},
                "session:revoke", max_attempts,
            )
            connection.execute(
                """UPDATE sync_queue SET state='inflight',claim_token=?,lease_owner=?,
                       lease_expires_at=?,last_attempt_at=?,attempt_count=attempt_count+1
                   WHERE id=? AND state='pending'""",
                (
                    claim_token, owner, iso(utc_now() + timedelta(seconds=lease_seconds)),
                    iso(), item_id,
                ),
            )
            return generation, item_id, claim_token

        return await self.database.write(operation)

    async def set_error(self, generation: int, code: str) -> None:
        await self.database.write(lambda connection: connection.execute(
            "UPDATE local_sessions SET last_error=?, updated_at=? WHERE singleton=1 AND generation=?",
            (code[:100], iso(), generation),
        ))

    async def clear(self, expected_generation: int | None = None) -> bool:
        def operation(connection: Any) -> bool:
            if expected_generation is None:
                cursor = connection.execute("DELETE FROM local_sessions WHERE singleton=1")
            else:
                cursor = connection.execute(
                    "DELETE FROM local_sessions WHERE singleton=1 AND generation=?",
                    (expected_generation,),
                )
            return cursor.rowcount == 1

        return await self.database.write(operation)


class CameraRepository:
    def __init__(self, database: Database, cipher: FieldCipher, queue_max_attempts: int = 8) -> None:
        self.database = database
        self.cipher = cipher
        self.queue_max_attempts = queue_max_attempts

    def _row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "server_id": row["server_id"], "name": row["name"],
            "stream_url": self.cipher.decrypt(row["stream_url_encrypted"], f"camera:{row['id']}"),
            "stream_type": row["stream_type"], "location_label": row["location_label"],
            "analytics_config": load(row["analytics_config_json"]), "zones": load(row["zones_json"]),
            "is_active": bool(row["is_active"]), "sync_state": row["sync_state"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    async def create(self, payload: CameraCreate, max_cameras: int) -> dict[str, Any]:
        camera_id, now = str(uuid4()), iso()
        encrypted = self.cipher.encrypt(payload.stream_url, f"camera:{camera_id}")
        central = CentralCameraCreate(id=UUID(camera_id), **payload.model_dump())

        def operation(connection: Any) -> Any:
            count = int(connection.execute("SELECT count(*) FROM local_cameras").fetchone()[0])
            if count >= max_cameras:
                raise ValueError("license camera limit reached")
            connection.execute(
                """INSERT INTO local_cameras(
                       id, name, stream_url_encrypted, stream_type, location_label,
                       analytics_config_json, zones_json, is_active, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    camera_id, payload.name, encrypted, payload.stream_type,
                    payload.location_label, dump(payload.analytics_config), dump(payload.zones),
                    1, now, now,
                ),
            )
            _enqueue(
                connection, "/api/v1/cameras/",
                {"_kind": "camera", "_method": "POST", "_local_id": camera_id,
                 "body": central.model_dump(mode="json")},
                f"camera:{camera_id}", self.queue_max_attempts,
            )
            return connection.execute("SELECT * FROM local_cameras WHERE id=?", (camera_id,)).fetchone()

        return self._row(await self.database.write(operation))

    async def list(self, limit: int, offset: int) -> dict[str, Any]:
        def operation(connection: Any) -> tuple[list[Any], int]:
            rows = connection.execute(
                "SELECT * FROM local_cameras ORDER BY name COLLATE NOCASE, id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = int(connection.execute("SELECT count(*) FROM local_cameras").fetchone()[0])
            return rows, total

        rows, total = await self.database.read(operation)
        return {"items": [self._row(row) for row in rows], "limit": limit, "offset": offset, "total": total}

    async def list_active(self, limit: int) -> list[dict[str, Any]]:
        """Return only runnable cameras for the process-local orchestrator."""
        if limit < 1:
            return []
        rows = await self.database.read(lambda connection: connection.execute(
            "SELECT * FROM local_cameras WHERE is_active=1 "
            "ORDER BY name COLLATE NOCASE, id LIMIT ?",
            (limit,),
        ).fetchall())
        return [self._row(row) for row in rows]

    async def get(self, camera_id: UUID | str) -> dict[str, Any] | None:
        row = await self.database.read(lambda connection: connection.execute(
            "SELECT * FROM local_cameras WHERE id=?", (str(camera_id),)
        ).fetchone())
        return self._row(row) if row else None

    async def update(self, camera_id: UUID | str, payload: CameraUpdate) -> dict[str, Any] | None:
        identifier, changes = str(camera_id), payload.model_dump(exclude_unset=True)

        def operation(connection: Any) -> Any:
            current = connection.execute("SELECT * FROM local_cameras WHERE id=?", (identifier,)).fetchone()
            if current is None:
                return None
            columns: dict[str, Any] = {}
            mapping = {"name": "name", "stream_type": "stream_type", "location_label": "location_label", "is_active": "is_active"}
            for key, column in mapping.items():
                if key in changes:
                    columns[column] = int(changes[key]) if key == "is_active" else changes[key]
            if "stream_url" in changes:
                columns["stream_url_encrypted"] = self.cipher.encrypt(changes["stream_url"], f"camera:{identifier}")
            if "analytics_config" in changes:
                columns["analytics_config_json"] = dump(changes["analytics_config"])
            if "zones" in changes:
                columns["zones_json"] = dump(changes["zones"])
            columns.update({"sync_state": "pending", "updated_at": iso()})
            assignments = ", ".join(f"{column}=?" for column in columns)
            connection.execute(f"UPDATE local_cameras SET {assignments} WHERE id=?", (*columns.values(), identifier))
            updated = connection.execute("SELECT * FROM local_cameras WHERE id=?", (identifier,)).fetchone()
            public = self._row(updated)
            latest = connection.execute(
                "SELECT state,payload_json,last_attempt_at FROM sync_queue WHERE logical_key=? ORDER BY version DESC LIMIT 1",
                (f"camera:{identifier}",),
            ).fetchone()
            if current["server_id"] is None and latest and latest["state"] == "pending" and latest["last_attempt_at"] is None and load(latest["payload_json"]).get("_method") == "POST":
                central = CentralCameraCreate(
                    id=UUID(identifier), name=public["name"], stream_url=public["stream_url"],
                    stream_type=public["stream_type"], location_label=public["location_label"],
                    analytics_config=public["analytics_config"], zones=public["zones"],
                )
                queued = {"_kind": "camera", "_method": "POST", "_local_id": identifier, "body": central.model_dump(mode="json")}
                endpoint = "/api/v1/cameras/"
            else:
                update_fields = {key: value for key, value in changes.items() if key not in {"analytics_config", "zones"}}
                central_update = CentralCameraUpdate.model_validate(update_fields)
                queued = {"_kind": "camera", "_method": "PATCH", "_local_id": identifier,
                          "body": central_update.model_dump(mode="json", exclude_unset=True)}
                if "analytics_config" in changes or "zones" in changes:
                    queued["analytics"] = {
                        "analytics_config": changes.get("analytics_config", public["analytics_config"]),
                        "zones": changes.get("zones", public["zones"]),
                    }
                endpoint = f"/api/v1/cameras/{identifier}"
            _enqueue(connection, endpoint, queued, f"camera:{identifier}", self.queue_max_attempts)
            return updated

        row = await self.database.write(operation)
        return self._row(row) if row else None

    async def delete(self, camera_id: UUID | str) -> bool:
        identifier = str(camera_id)

        def operation(connection: Any) -> bool:
            row = connection.execute("SELECT server_id FROM local_cameras WHERE id=?", (identifier,)).fetchone()
            if row is None:
                return False
            latest = connection.execute(
                "SELECT * FROM sync_queue WHERE logical_key=? ORDER BY version DESC LIMIT 1",
                (f"camera:{identifier}",),
            ).fetchone()
            if row["server_id"] is None and latest and latest["state"] in {"pending", "retry_wait"}:
                connection.execute(
                    """UPDATE sync_queue SET state='cancelled',completed_at=?
                       WHERE (id=? OR depends_on_id=?) AND state IN ('pending','retry_wait')""",
                    (iso(), latest["id"], latest["id"]),
                )
            else:
                _enqueue(
                    connection, f"/api/v1/cameras/{identifier}",
                    {"_kind": "camera", "_method": "DELETE", "_local_id": identifier, "body": {}},
                    f"camera:{identifier}", self.queue_max_attempts,
                )
            connection.execute("DELETE FROM local_cameras WHERE id=?", (identifier,))
            return True

        return await self.database.write(operation)


class AnalyticsRepository:
    def __init__(self, database: Database, queue_max_attempts: int = 8) -> None:
        self.database = database
        self.queue_max_attempts = queue_max_attempts

    def _camera_dependency(self, connection: Any, camera_id: str) -> str | None:
        camera = connection.execute("SELECT server_id FROM local_cameras WHERE id=?", (camera_id,)).fetchone()
        if camera is None:
            raise ValueError("camera does not exist")
        if camera["server_id"] is not None:
            return None
        row = connection.execute(
            "SELECT id FROM sync_queue WHERE logical_key=? ORDER BY version ASC LIMIT 1",
            (f"camera:{camera_id}",),
        ).fetchone()
        if row is None:
            raise ValueError("camera identity has no synchronization record")
        return str(row["id"])

    async def add_event(self, payload: AnalyticsEventCreate) -> dict[str, Any]:
        event_id = payload.id or uuid4()
        image_id = require_central_image_id(payload.captured_image_path, payload.captured_image_id)
        central = CentralAnalyticsEvent(
            id=event_id, camera_id=payload.camera_id, event_type=payload.event_type,
            payload=payload.payload, captured_image_id=image_id, created_at=payload.created_at,
        )

        def operation(connection: Any) -> None:
            dependency = self._camera_dependency(connection, str(payload.camera_id))
            connection.execute(
                """INSERT INTO local_analytics_events(
                       id,camera_id,event_type,payload_json,captured_image_path,synced,created_at,captured_image_id
                   ) VALUES (?,?,?,?,?,0,?,?)""",
                (str(event_id), str(payload.camera_id), payload.event_type, dump(payload.payload),
                 payload.captured_image_path, iso(payload.created_at), str(image_id) if image_id else None),
            )
            _enqueue(connection, "/api/v1/sync/events", {"events": [central.model_dump(mode="json")]},
                     f"event:{event_id}", self.queue_max_attempts, dependency)

        await self.database.write(operation)
        return central.model_dump(mode="json")

    async def add_alert(self, payload: AlertCreate) -> dict[str, Any]:
        alert_id = payload.id or uuid4()
        image_id = require_central_image_id(payload.image_path, payload.captured_image_id)
        central = CentralAlert(
            id=alert_id, camera_id=payload.camera_id, zone_id=payload.zone_id,
            captured_image_id=image_id, confidence=payload.confidence,
            resolved=payload.resolved, created_at=payload.created_at,
        )

        def operation(connection: Any) -> None:
            dependency = self._camera_dependency(connection, str(payload.camera_id))
            connection.execute(
                """INSERT INTO local_alerts(
                       id,camera_id,zone_id,image_path,confidence,resolved,synced,created_at,captured_image_id
                   ) VALUES (?,?,?,?,?,?,0,?,?)""",
                (str(alert_id), str(payload.camera_id), payload.zone_id, payload.image_path,
                 payload.confidence, int(payload.resolved), iso(payload.created_at), str(image_id) if image_id else None),
            )
            _enqueue(connection, "/api/v1/sync/alerts", {"alerts": [central.model_dump(mode="json")]},
                     f"alert:{alert_id}", self.queue_max_attempts, dependency)

        await self.database.write(operation)
        return central.model_dump(mode="json")

    async def add_people_count(self, payload: PeopleCountCreate) -> dict[str, Any]:
        count_id = payload.id or uuid4()
        central = CentralPeopleCount(
            id=count_id, camera_id=payload.camera_id, count_in=payload.count_in,
            count_out=payload.count_out, timestamp=payload.captured_at,
        )

        def operation(connection: Any) -> None:
            dependency = self._camera_dependency(connection, str(payload.camera_id))
            connection.execute(
                "INSERT INTO local_people_counts VALUES (?, ?, ?, ?, ?, 0)",
                (str(count_id), str(payload.camera_id), payload.count_in, payload.count_out, iso(payload.captured_at)),
            )
            _enqueue(connection, "/api/v1/sync/people-count", {"snapshots": [central.model_dump(mode="json")]},
                     f"count:{count_id}", self.queue_max_attempts, dependency)

        await self.database.write(operation)
        return central.model_dump(mode="json")

    async def list_events(self, limit: int, offset: int) -> dict[str, Any]:
        def operation(connection: Any) -> tuple[list[Any], int]:
            rows = connection.execute(
                "SELECT * FROM local_analytics_events ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = int(connection.execute("SELECT count(*) FROM local_analytics_events").fetchone()[0])
            return rows, total

        rows, total = await self.database.read(operation)
        items = [{
            "id": row["id"], "camera_id": row["camera_id"], "event_type": row["event_type"],
            "payload": load(row["payload_json"]), "captured_image_path": row["captured_image_path"],
            "captured_image_id": row["captured_image_id"], "synced": bool(row["synced"]),
            "created_at": row["created_at"],
        } for row in rows]
        return {"items": items, "limit": limit, "offset": offset, "total": total}


class PersonRepository:
    def __init__(self, database: Database, cipher: FieldCipher) -> None:
        self.database = database
        self.cipher = cipher

    async def replace_cache(self, people: list[PersonCacheEntry]) -> None:
        def operation(connection: Any) -> None:
            server_ids = [str(person.server_id) for person in people]
            for person in people:
                identifier = str(person.server_id)
                phone = self.cipher.encrypt(person.phone, f"person-phone:{identifier}")
                connection.execute(
                    """INSERT INTO local_persons(id,server_id,name,phone_encrypted,face_encoding_path,synced_at)
                       VALUES (?,?,?,?,?,?) ON CONFLICT(server_id) DO UPDATE SET
                       name=excluded.name,phone_encrypted=excluded.phone_encrypted,
                       face_encoding_path=excluded.face_encoding_path,synced_at=excluded.synced_at""",
                    (identifier, identifier, person.name, phone, person.face_encoding_path, iso(person.synced_at)),
                )
            if server_ids:
                placeholders = ",".join("?" for _ in server_ids)
                connection.execute(f"DELETE FROM local_persons WHERE server_id NOT IN ({placeholders})", server_ids)
            else:
                connection.execute("DELETE FROM local_persons")

        await self.database.write(operation)

    async def list(self, limit: int, offset: int) -> dict[str, Any]:
        def operation(connection: Any) -> tuple[list[Any], int]:
            rows = connection.execute(
                "SELECT * FROM local_persons ORDER BY name COLLATE NOCASE,id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = int(connection.execute("SELECT count(*) FROM local_persons").fetchone()[0])
            return rows, total

        rows, total = await self.database.read(operation)
        items = [{
            "id": row["id"], "server_id": row["server_id"], "name": row["name"],
            "phone": self.cipher.decrypt(row["phone_encrypted"], f"person-phone:{row['server_id']}"),
            "face_encoding_path": row["face_encoding_path"], "synced_at": row["synced_at"],
        } for row in rows]
        return {"items": items, "limit": limit, "offset": offset, "total": total}


class SyncQueueRepository:
    def __init__(self, database: Database, lease_seconds: int, max_attempts: int = 8) -> None:
        self.database = database
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    async def enqueue_revocation(self, generation: int) -> str:
        return await self.database.write(lambda connection: _enqueue(
            connection, "/api/v1/auth/logout",
            {"_kind": "session_revoke", "generation": generation},
            "session:revoke", self.max_attempts,
        ))

    async def count(self, states: tuple[str, ...] = ("pending", "inflight", "retry_wait")) -> int:
        placeholders = ",".join("?" for _ in states)
        return await self.database.read(lambda connection: int(connection.execute(
            f"SELECT count(*) FROM sync_queue WHERE state IN ({placeholders})", states
        ).fetchone()[0]))

    async def dead_letter_count(self) -> int:
        return await self.count(("dead_letter",))

    async def claim(
        self, owner: str, limit: int = 100, logical_key: str | None = None
    ) -> list[dict[str, Any]]:
        if not owner or not 1 <= limit <= 500:
            raise ValueError("owner and claim limit are invalid")
        token, now = str(uuid4()), utc_now()
        expires = now + timedelta(seconds=self.lease_seconds)

        def operation(connection: Any) -> list[Any]:
            connection.execute(
                """UPDATE sync_queue SET state='retry_wait',claim_token=NULL,lease_owner=NULL,
                       lease_expires_at=NULL,next_attempt_at=?
                   WHERE state='inflight' AND lease_expires_at < ?""",
                (iso(now), iso(now)),
            )
            key_clause = "AND q.logical_key=?" if logical_key else ""
            parameters: tuple[Any, ...] = (iso(now), logical_key, limit) if logical_key else (iso(now), limit)
            rows = connection.execute(
                """SELECT q.id FROM sync_queue q
                   WHERE q.state IN ('pending','retry_wait') AND q.next_attempt_at <= ?
                     {key_clause}
                     AND (q.predecessor_id IS NULL OR EXISTS(
                         SELECT 1 FROM sync_queue p WHERE p.id=q.predecessor_id
                         AND p.state IN ('succeeded','cancelled')))
                     AND (q.depends_on_id IS NULL OR EXISTS(
                         SELECT 1 FROM sync_queue d WHERE d.id=q.depends_on_id
                         AND d.state='succeeded'))
                   ORDER BY q.created_at,q.version,q.id LIMIT ?""".format(key_clause=key_clause),
                parameters,
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"""UPDATE sync_queue SET state='inflight',claim_token=?,lease_owner=?,
                       lease_expires_at=?,last_attempt_at=?,attempt_count=attempt_count+1
                   WHERE id IN ({placeholders}) AND state IN ('pending','retry_wait')""",
                (token, owner, iso(expires), iso(now), *ids),
            )
            return connection.execute(
                "SELECT * FROM sync_queue WHERE claim_token=? AND lease_owner=? ORDER BY created_at,version,id",
                (token, owner),
            ).fetchall()

        rows = await self.database.write(operation)
        return [{
            "id": row["id"], "endpoint": row["endpoint"], "payload": load(row["payload_json"]),
            "attempt_count": row["attempt_count"], "claim_token": row["claim_token"],
            "lease_owner": row["lease_owner"],
        } for row in rows]

    def _owned_clause(self) -> str:
        return "id=? AND claim_token=? AND lease_owner=? AND state='inflight' AND lease_expires_at>?"

    async def complete(self, item_id: str, claim_token: str, owner: str) -> bool:
        now = iso()

        def operation(connection: Any) -> bool:
            row = connection.execute(
                f"SELECT logical_key FROM sync_queue WHERE {self._owned_clause()}",
                (item_id, claim_token, owner, now),
            ).fetchone()
            if row is None:
                return False
            cursor = connection.execute(
                f"UPDATE sync_queue SET state='succeeded',completed_at=?,claim_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE {self._owned_clause()}",
                (now, item_id, claim_token, owner, now),
            )
            if cursor.rowcount != 1:
                return False
            logical_key = str(row["logical_key"])
            table = None
            if logical_key.startswith("event:"):
                table = "local_analytics_events"
            elif logical_key.startswith("alert:"):
                table = "local_alerts"
            elif logical_key.startswith("count:"):
                table = "local_people_counts"
            if table is not None:
                connection.execute(
                    f"UPDATE {table} SET synced=1 WHERE id=?",
                    (logical_key.split(":", 1)[1],),
                )
            return True

        return await self.database.write(operation)

    async def complete_camera(
        self, item_id: str, claim_token: str, owner: str, local_id: str, server_id: str
    ) -> bool:
        now = iso()

        def operation(connection: Any) -> bool:
            cursor = connection.execute(
                f"UPDATE sync_queue SET state='succeeded',completed_at=?,claim_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE {self._owned_clause()}",
                (now, item_id, claim_token, owner, now),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                "UPDATE local_cameras SET server_id=?,sync_state='synced' WHERE id=?",
                (server_id, local_id),
            )
            return True

        return await self.database.write(operation)

    async def complete_revocation(
        self, item_id: str, claim_token: str, owner: str, generation: int
    ) -> bool:
        now = iso()

        def operation(connection: Any) -> bool:
            cursor = connection.execute(
                f"UPDATE sync_queue SET state='succeeded',completed_at=?,claim_token=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE {self._owned_clause()}",
                (now, item_id, claim_token, owner, now),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                "DELETE FROM local_sessions WHERE singleton=1 AND generation=? AND status='revocation_pending'",
                (generation,),
            )
            return True

        return await self.database.write(operation)

    async def fail(
        self, item_id: str, claim_token: str, owner: str,
        error_code: str, error_message: str, permanent: bool,
    ) -> bool:
        now = utc_now()

        def operation(connection: Any) -> bool:
            row = connection.execute(
                f"SELECT attempt_count,max_attempts FROM sync_queue WHERE {self._owned_clause()}",
                (item_id, claim_token, owner, iso(now)),
            ).fetchone()
            if row is None:
                return False
            dead = permanent or int(row["attempt_count"]) >= int(row["max_attempts"])
            state = "dead_letter" if dead else "retry_wait"
            delay = min(300, 2 ** min(int(row["attempt_count"]), 8))
            cursor = connection.execute(
                """UPDATE sync_queue SET state=?,claim_token=NULL,lease_owner=NULL,
                       lease_expires_at=NULL,next_attempt_at=?,last_error_code=?,last_error_message=?,
                       failure_class=?,failed_at=? WHERE id=? AND claim_token=? AND lease_owner=?""",
                (
                    state, iso(now + timedelta(seconds=delay)), error_code[:100],
                    error_message[:200], "permanent" if permanent else "transient",
                    iso(now) if dead else None, item_id, claim_token, owner,
                ),
            )
            return cursor.rowcount == 1

        return await self.database.write(operation)

    async def list_dead_letters(self, limit: int, offset: int) -> dict[str, Any]:
        def operation(connection: Any) -> tuple[list[Any], int]:
            rows = connection.execute(
                """SELECT id,logical_key,endpoint,attempt_count,max_attempts,last_error_code,
                          last_error_message,failure_class,failed_at,created_at
                   FROM sync_queue WHERE state='dead_letter'
                   ORDER BY failed_at DESC,id LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            total = int(connection.execute(
                "SELECT count(*) FROM sync_queue WHERE state='dead_letter'"
            ).fetchone()[0])
            return rows, total

        rows, total = await self.database.read(operation)
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset, "total": total}

    async def retry_dead_letter(self, item_id: UUID | str) -> bool:
        return await self.database.write(lambda connection: connection.execute(
            """UPDATE sync_queue SET state='pending',attempt_count=0,next_attempt_at=?,
                   failed_at=NULL,failure_class=NULL,last_error_code=NULL,last_error_message=NULL
               WHERE id=? AND state='dead_letter'""",
            (iso(), str(item_id)),
        ).rowcount == 1)

    async def discard_dead_letter(self, item_id: UUID | str) -> bool:
        return await self.database.write(lambda connection: connection.execute(
            "UPDATE sync_queue SET state='cancelled',completed_at=? WHERE id=? AND state='dead_letter'",
            (iso(), str(item_id)),
        ).rowcount == 1)

    async def purge_retained(self, succeeded_days: int, dead_letter_days: int) -> int:
        succeeded_before = iso(utc_now() - timedelta(days=succeeded_days))
        dead_before = iso(utc_now() - timedelta(days=dead_letter_days))

        def operation(connection: Any) -> int:
            candidates = connection.execute(
                """SELECT id,state FROM sync_queue WHERE
                     (state IN ('succeeded','cancelled') AND completed_at < ?)
                     OR (state='dead_letter' AND failed_at < ?)
                   ORDER BY created_at,id""",
                (succeeded_before, dead_before),
            ).fetchall()
            removed = 0
            for candidate in candidates:
                item_id, state = candidate["id"], candidate["state"]
                if state == "dead_letter":
                    descendants = connection.execute(
                        """WITH RECURSIVE descendants(id) AS (
                               SELECT id FROM sync_queue
                                WHERE predecessor_id=? OR depends_on_id=?
                               UNION
                               SELECT q.id FROM sync_queue q JOIN descendants d
                                 ON q.predecessor_id=d.id OR q.depends_on_id=d.id
                           ) SELECT id FROM descendants""",
                        (item_id, item_id),
                    ).fetchall()
                    descendant_ids = [row["id"] for row in descendants]
                    if descendant_ids:
                        placeholders = ",".join("?" for _ in descendant_ids)
                        connection.execute(
                            f"""UPDATE sync_queue SET state='cancelled',completed_at=?,
                                   claim_token=NULL,lease_owner=NULL,lease_expires_at=NULL
                                WHERE id IN ({placeholders}) AND state!='inflight'""",
                            (iso(), *descendant_ids),
                        )
                elif state == "cancelled":
                    connection.execute(
                        """UPDATE sync_queue SET state='cancelled',completed_at=?,
                               claim_token=NULL,lease_owner=NULL,lease_expires_at=NULL
                           WHERE depends_on_id=? AND state!='inflight'""",
                        (iso(), item_id),
                    )
                connection.execute(
                    "UPDATE sync_queue SET predecessor_id=NULL WHERE predecessor_id=?",
                    (item_id,),
                )
                connection.execute(
                    "UPDATE sync_queue SET depends_on_id=NULL WHERE depends_on_id=?",
                    (item_id,),
                )
                removed += connection.execute(
                    "DELETE FROM sync_queue WHERE id=?", (item_id,)
                ).rowcount
            return removed

        return await self.database.write(operation)
