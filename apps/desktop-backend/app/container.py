"""Composition root shared with later desktop orchestration."""

from __future__ import annotations

from datetime import datetime

from app.clients import CentralApiClient
from app.authorization import AuthorizationService
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
from app.services import AnalyticsService, AuthService, CameraService, PersonService, SyncService


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings)
        self.cipher = FieldCipher(settings.field_encryption_key_bytes)
        self.central = CentralApiClient(settings)
        self.sessions = SessionRepository(self.database, self.cipher)
        self.cameras = CameraRepository(
            self.database, self.cipher, settings.queue_max_attempts
        )
        self.analytics = AnalyticsRepository(self.database, settings.queue_max_attempts)
        self.persons = PersonRepository(self.database, self.cipher)
        self.queue = SyncQueueRepository(
            self.database, settings.queue_lease_seconds, settings.queue_max_attempts
        )
        self.authorization = AuthorizationService()
        self.auth = AuthService(self.sessions, self.central, self.queue)
        self.camera_service = CameraService(self.cameras, self.authorization)
        self.analytics_service = AnalyticsService(self.analytics, self.authorization)
        self.person_service = PersonService(self.persons, self.authorization)
        self.sync = SyncService(
            self.queue, self.sessions, self.central,
            settings.queue_succeeded_retention_days,
            settings.queue_dead_letter_retention_days,
        )
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
