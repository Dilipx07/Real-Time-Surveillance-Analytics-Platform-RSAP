"""Composition root shared with later desktop orchestration."""

from __future__ import annotations

from datetime import datetime

from app.clients import CentralApiClient
from app.config import Settings
from app.crypto import FieldCipher
from app.database import Database
from app.repositories import (
    AnalyticsRepository,
    CameraRepository,
    PersonRepository,
    SessionRepository,
    SyncQueueRepository,
)
from app.services import AuthService, SyncService


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings)
        self.cipher = FieldCipher(settings.field_encryption_key_bytes)
        self.central = CentralApiClient(settings)
        self.sessions = SessionRepository(self.database, self.cipher)
        self.cameras = CameraRepository(self.database, self.cipher)
        self.analytics = AnalyticsRepository(self.database)
        self.persons = PersonRepository(self.database, self.cipher)
        self.queue = SyncQueueRepository(self.database, settings.queue_lease_seconds)
        self.auth = AuthService(self.sessions, self.central)
        self.sync = SyncService(self.queue, self.sessions, self.cameras, self.central)
        self.connected = False
        self.last_connectivity_check: datetime | None = None
        self.last_connectivity_error: str | None = None

    async def start(self) -> None:
        await self.database.migrate()
        await self.database.verify()

    async def close(self) -> None:
        try:
            await self.central.close()
        finally:
            await self.database.close()
