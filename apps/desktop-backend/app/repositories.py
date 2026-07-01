"""Transaction-safe repositories for local edge state."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.crypto import FieldCipher
from app.database import Database
from app.schemas import (
    AlertCreate,
    AnalyticsEventCreate,
    CameraCreate,
    CameraUpdate,
    LocalSession,
    PeopleCountCreate,
    PersonCacheEntry,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).isoformat()


def dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def load(value: str) -> Any:
    return json.loads(value)


def _queue(
    connection: Any,
    endpoint: str,
    payload: dict[str, Any],
    dedupe_key: str,
) -> None:
    now = iso()
    connection.execute(
        """INSERT INTO sync_queue(
               id, endpoint, payload_json, dedupe_key, next_attempt_at, created_at
           ) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(dedupe_key) DO UPDATE SET
               payload_json=excluded.payload_json,
               state='pending', next_attempt_at=excluded.next_attempt_at,
               lease_expires_at=NULL, claim_token=NULL, lease_owner=NULL""",
        (str(uuid4()), endpoint, dump(payload), dedupe_key, now, now),
    )


class SessionRepository:
    def __init__(self, database: Database, cipher: FieldCipher) -> None:
        self.database = database
        self.cipher = cipher

    async def save(self, session: LocalSession, license_valid_until: datetime | None) -> None:
        encrypted = self.cipher.encrypt_json(session.model_dump(mode="json"), "local-session")
        user_id = str(session.user.get("id", ""))
        if not user_id:
            raise ValueError("central login response did not include user.id")

        def operation(connection: Any) -> None:
            connection.execute(
                """INSERT INTO local_sessions(
                       singleton, encrypted_payload, user_id, license_valid_until, updated_at
                   ) VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET
                       encrypted_payload=excluded.encrypted_payload,
                       user_id=excluded.user_id,
                       license_valid_until=excluded.license_valid_until,
                       updated_at=excluded.updated_at""",
                (encrypted, user_id, iso(license_valid_until) if license_valid_until else None, iso()),
            )

        await self.database.write(operation)

    async def get(self) -> tuple[LocalSession, datetime | None] | None:
        def operation(connection: Any) -> Any:
            return connection.execute(
                "SELECT encrypted_payload, license_valid_until FROM local_sessions WHERE singleton=1"
            ).fetchone()

        row = await self.database.read(operation)
        if row is None:
            return None
        payload = self.cipher.decrypt_json(row["encrypted_payload"], "local-session")
        expiry = datetime.fromisoformat(row["license_valid_until"]) if row["license_valid_until"] else None
        return LocalSession.model_validate(payload), expiry

    async def clear(self) -> None:
        await self.database.write(
            lambda connection: connection.execute("DELETE FROM local_sessions WHERE singleton=1")
        )


class CameraRepository:
    def __init__(self, database: Database, cipher: FieldCipher) -> None:
        self.database = database
        self.cipher = cipher

    def _row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "server_id": row["server_id"],
            "name": row["name"],
            "stream_url": self.cipher.decrypt(row["stream_url_encrypted"], f"camera:{row['id']}"),
            "stream_type": row["stream_type"],
            "location_label": row["location_label"],
            "analytics_config": load(row["analytics_config_json"]),
            "zones": load(row["zones_json"]),
            "is_active": bool(row["is_active"]),
            "sync_state": row["sync_state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def create(self, payload: CameraCreate) -> dict[str, Any]:
        camera_id, now = str(uuid4()), iso()
        encrypted = self.cipher.encrypt(payload.stream_url, f"camera:{camera_id}")

        def operation(connection: Any) -> Any:
            connection.execute(
                """INSERT INTO local_cameras(
                       id, name, stream_url_encrypted, stream_type, location_label,
                       analytics_config_json, zones_json, is_active, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    camera_id, payload.name, encrypted, payload.stream_type,
                    payload.location_label, dump(payload.analytics_config), dump(payload.zones),
                    int(payload.is_active), now, now,
                ),
            )
            _queue(connection, "/api/v1/cameras/", {
                "_method": "POST", "_local_id": camera_id,
                "body": payload.model_dump(mode="json", exclude={"is_active"}),
            }, f"camera:{camera_id}")
            return connection.execute("SELECT * FROM local_cameras WHERE id=?", (camera_id,)).fetchone()

        return self._row(await self.database.write(operation))

    async def list(self) -> list[dict[str, Any]]:
        rows = await self.database.read(
            lambda connection: connection.execute(
                "SELECT * FROM local_cameras ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        )
        return [self._row(row) for row in rows]

    async def get(self, camera_id: UUID | str) -> dict[str, Any] | None:
        row = await self.database.read(
            lambda connection: connection.execute(
                "SELECT * FROM local_cameras WHERE id=?", (str(camera_id),)
            ).fetchone()
        )
        return self._row(row) if row else None

    async def update(self, camera_id: UUID | str, payload: CameraUpdate) -> dict[str, Any] | None:
        identifier = str(camera_id)
        changes = payload.model_dump(exclude_unset=True)

        def operation(connection: Any) -> Any:
            current = connection.execute("SELECT * FROM local_cameras WHERE id=?", (identifier,)).fetchone()
            if current is None:
                return None
            columns: dict[str, Any] = {}
            mapping = {
                "name": "name", "stream_type": "stream_type",
                "location_label": "location_label", "is_active": "is_active",
            }
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
            connection.execute(
                f"UPDATE local_cameras SET {assignments} WHERE id=?",
                (*columns.values(), identifier),
            )
            updated = connection.execute("SELECT * FROM local_cameras WHERE id=?", (identifier,)).fetchone()
            public = self._row(updated)
            if current["server_id"] is None:
                create_body = {
                    key: public[key]
                    for key in (
                        "name", "stream_url", "stream_type", "location_label",
                        "analytics_config", "zones",
                    )
                }
                queued = {"_method": "POST", "_local_id": identifier, "body": create_body}
                endpoint = "/api/v1/cameras/"
            else:
                body = {
                    key: value
                    for key, value in changes.items()
                    if key not in {"analytics_config", "zones"}
                }
                if "analytics_config" in changes or "zones" in changes:
                    body["_analytics"] = {
                        "analytics_config": changes.get("analytics_config", public["analytics_config"]),
                        "zones": changes.get("zones", public["zones"]),
                    }
                queued = {"_method": "PATCH", "_local_id": identifier, "body": body}
                endpoint = f"/api/v1/cameras/{current['server_id']}"
            _queue(connection, endpoint, queued, f"camera:{identifier}")
            return updated

        row = await self.database.write(operation)
        return self._row(row) if row else None

    async def delete(self, camera_id: UUID | str) -> bool:
        identifier = str(camera_id)

        def operation(connection: Any) -> bool:
            row = connection.execute(
                "SELECT server_id FROM local_cameras WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                return False
            if row["server_id"]:
                _queue(connection, f"/api/v1/cameras/{row['server_id']}", {
                    "_method": "DELETE", "_local_id": identifier, "body": {},
                }, f"camera:{identifier}")
            else:
                connection.execute("DELETE FROM sync_queue WHERE dedupe_key=?", (f"camera:{identifier}",))
            connection.execute("DELETE FROM local_cameras WHERE id=?", (identifier,))
            return True

        return await self.database.write(operation)

    async def mark_synced(self, local_id: str, server_id: str | None = None) -> None:
        def operation(connection: Any) -> None:
            if server_id:
                connection.execute(
                    "UPDATE local_cameras SET server_id=?, sync_state='synced' WHERE id=?",
                    (server_id, local_id),
                )
            else:
                connection.execute(
                    "UPDATE local_cameras SET sync_state='synced' WHERE id=?", (local_id,)
                )

        await self.database.write(operation)


class AnalyticsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def add_event(self, payload: AnalyticsEventCreate) -> dict[str, Any]:
        event_id = str(payload.id or uuid4())
        item = {**payload.model_dump(mode="json"), "id": event_id}

        def operation(connection: Any) -> None:
            connection.execute(
                "INSERT INTO local_analytics_events VALUES (?, ?, ?, ?, ?, 0, ?)",
                (event_id, str(payload.camera_id), payload.event_type, dump(payload.payload),
                 payload.captured_image_path, iso(payload.created_at)),
            )
            _queue(connection, "/api/v1/sync/events", {"events": [item]}, f"event:{event_id}")

        await self.database.write(operation)
        return item

    async def add_alert(self, payload: AlertCreate) -> dict[str, Any]:
        alert_id = str(payload.id or uuid4())
        item = {**payload.model_dump(mode="json"), "id": alert_id}

        def operation(connection: Any) -> None:
            connection.execute(
                "INSERT INTO local_alerts VALUES (?, ?, ?, ?, ?, 0, 0, ?)",
                (alert_id, str(payload.camera_id), payload.zone_id, payload.image_path,
                 payload.confidence, iso(payload.created_at)),
            )
            _queue(connection, "/api/v1/sync/alerts", {"alerts": [item]}, f"alert:{alert_id}")

        await self.database.write(operation)
        return item

    async def add_people_count(self, payload: PeopleCountCreate) -> dict[str, Any]:
        count_id = str(payload.id or uuid4())
        item = {**payload.model_dump(mode="json"), "id": count_id}

        def operation(connection: Any) -> None:
            connection.execute(
                "INSERT INTO local_people_counts VALUES (?, ?, ?, ?, ?, 0)",
                (count_id, str(payload.camera_id), payload.count_in, payload.count_out,
                 iso(payload.captured_at)),
            )
            _queue(connection, "/api/v1/sync/people-count", {"snapshots": [item]}, f"count:{count_id}")

        await self.database.write(operation)
        return item

    async def list_events(self, limit: int, offset: int) -> list[dict[str, Any]]:
        rows = await self.database.read(lambda connection: connection.execute(
            "SELECT * FROM local_analytics_events ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall())
        return [{
            "id": row["id"], "camera_id": row["camera_id"], "event_type": row["event_type"],
            "payload": load(row["payload_json"]), "captured_image_path": row["captured_image_path"],
            "synced": bool(row["synced"]), "created_at": row["created_at"],
        } for row in rows]


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
                    """INSERT INTO local_persons(
                           id, server_id, name, phone_encrypted, face_encoding_path, synced_at
                       ) VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(server_id) DO UPDATE SET name=excluded.name,
                           phone_encrypted=excluded.phone_encrypted,
                           face_encoding_path=excluded.face_encoding_path,
                           synced_at=excluded.synced_at""",
                    (identifier, identifier, person.name, phone, person.face_encoding_path, iso(person.synced_at)),
                )
            if server_ids:
                placeholders = ",".join("?" for _ in server_ids)
                connection.execute(
                    f"DELETE FROM local_persons WHERE server_id NOT IN ({placeholders})", server_ids
                )
            else:
                connection.execute("DELETE FROM local_persons")

        await self.database.write(operation)

    async def list(self) -> list[dict[str, Any]]:
        rows = await self.database.read(lambda connection: connection.execute(
            "SELECT * FROM local_persons ORDER BY name COLLATE NOCASE, id"
        ).fetchall())
        return [{
            "id": row["id"], "server_id": row["server_id"], "name": row["name"],
            "phone": self.cipher.decrypt(row["phone_encrypted"], f"person-phone:{row['server_id']}"),
            "face_encoding_path": row["face_encoding_path"], "synced_at": row["synced_at"],
        } for row in rows]


class SyncQueueRepository:
    def __init__(self, database: Database, lease_seconds: int) -> None:
        self.database = database
        self.lease_seconds = lease_seconds

    async def count(self) -> int:
        return await self.database.read(lambda connection: int(connection.execute(
            "SELECT count(*) FROM sync_queue"
        ).fetchone()[0]))

    async def claim(self, owner: str, limit: int = 100) -> list[dict[str, Any]]:
        token, now = str(uuid4()), utc_now()
        expires = now + timedelta(seconds=self.lease_seconds)

        def operation(connection: Any) -> list[Any]:
            connection.execute(
                """UPDATE sync_queue SET state='pending', claim_token=NULL,
                       lease_owner=NULL, lease_expires_at=NULL
                   WHERE state='inflight' AND lease_expires_at < ?""",
                (iso(now),),
            )
            rows = connection.execute(
                """SELECT id FROM sync_queue
                   WHERE state='pending' AND next_attempt_at <= ?
                   ORDER BY created_at, id LIMIT ?""",
                (iso(now), limit),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"""UPDATE sync_queue SET state='inflight', claim_token=?, lease_owner=?,
                       lease_expires_at=?, last_attempted_at=?, attempts=attempts+1
                       WHERE id IN ({placeholders}) AND state='pending'""",
                (token, owner, iso(expires), iso(now), *ids),
            )
            return connection.execute(
                "SELECT * FROM sync_queue WHERE claim_token=? ORDER BY created_at, id", (token,)
            ).fetchall()

        rows = await self.database.write(operation)
        return [{
            "id": row["id"], "endpoint": row["endpoint"], "payload": load(row["payload_json"]),
            "attempts": row["attempts"], "claim_token": row["claim_token"],
        } for row in rows]

    async def complete(self, item_id: str, claim_token: str) -> bool:
        def operation(connection: Any) -> bool:
            cursor = connection.execute(
                "DELETE FROM sync_queue WHERE id=? AND claim_token=? AND state='inflight'",
                (item_id, claim_token),
            )
            return cursor.rowcount == 1

        return await self.database.write(operation)

    async def fail(self, item_id: str, claim_token: str, error_code: str) -> bool:
        def operation(connection: Any) -> bool:
            row = connection.execute(
                "SELECT attempts FROM sync_queue WHERE id=? AND claim_token=? AND state='inflight'",
                (item_id, claim_token),
            ).fetchone()
            if row is None:
                return False
            delay = min(300, 2 ** min(int(row["attempts"]), 8))
            cursor = connection.execute(
                """UPDATE sync_queue SET state='pending', claim_token=NULL, lease_owner=NULL,
                       lease_expires_at=NULL, next_attempt_at=?, last_error=?
                   WHERE id=? AND claim_token=?""",
                (iso(utc_now() + timedelta(seconds=delay)), error_code[:100], item_id, claim_token),
            )
            return cursor.rowcount == 1

        return await self.database.write(operation)
