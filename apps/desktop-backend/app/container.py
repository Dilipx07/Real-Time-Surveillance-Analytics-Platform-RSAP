"""Composition root shared with later desktop orchestration."""

from __future__ import annotations

import asyncio
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
from app.orchestration import CameraOrchestrationService, CameraWorkerManager
from app.orchestration.adapters import (
    Agent2CameraCatalog,
    Agent2EventSink,
    AsyncioPeriodicScheduler,
)
from app.orchestration.protocols import CaptureFactory, PipelineFactory


class Container:
    def __init__(
        self,
        settings: Settings,
        *,
        capture_factory: CaptureFactory | None = None,
        pipeline_factory: PipelineFactory | None = None,
        scheduler: AsyncioPeriodicScheduler | None = None,
    ) -> None:
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
        self.camera_catalog = Agent2CameraCatalog(
            self.cameras, self.sessions, self.authorization
        )
        self.event_sink = Agent2EventSink(self.analytics_service, self.sessions)
        manager_options: dict[str, object] = {}
        if capture_factory is not None:
            manager_options["capture_factory"] = capture_factory
        if pipeline_factory is not None:
            manager_options["pipeline_factory"] = pipeline_factory
        self.camera_manager = CameraWorkerManager(
            self.event_sink,
            **manager_options,  # type: ignore[arg-type]
        )
        self.orchestration_scheduler = scheduler or AsyncioPeriodicScheduler()
        self.orchestration = CameraOrchestrationService(
            self.camera_manager,
            self.camera_catalog,
            scheduler=self.orchestration_scheduler,
        )
        self.connected = False
        self.last_connectivity_check: datetime | None = None
        self.last_connectivity_error: str | None = None
        self._started = False
        self._close_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._started:
            return
        await self.database.migrate()
        await self.database.verify()
        try:
            await self.orchestration.start()
        except asyncio.CancelledError as startup_error:
            cleanup_task = asyncio.create_task(
                self.close(), name="desktop-startup-failure-cleanup"
            )
            try:
                await _settle_task(cleanup_task)
            except BaseException as cleanup_error:
                raise startup_error from cleanup_error
            raise
        except Exception:
            cleanup_task = asyncio.create_task(
                self.close(), name="desktop-startup-failure-cleanup"
            )
            await _settle_task(cleanup_task)
            raise
        self._started = True

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close_impl(), name="desktop-container-close"
            )
        await asyncio.shield(self._close_task)

    async def _close_impl(self) -> None:
        first_error: BaseException | None = None
        try:
            await self.orchestration.stop()
        except BaseException as error:
            first_error = error
        try:
            await self.orchestration_scheduler.shutdown()
        except BaseException as error:
            first_error = first_error or error
        try:
            await self.central.close()
        except BaseException as error:
            first_error = first_error or error
        try:
            await self.database.close()
        except BaseException as error:
            first_error = first_error or error
        self._started = False
        if first_error is not None:
            raise first_error


async def _settle_task(task: asyncio.Task[object]) -> None:
    """Wait for full container cleanup despite repeated caller cancellation."""
    while True:
        try:
            await asyncio.shield(task)
            return
        except asyncio.CancelledError:
            if task.done():
                task.result()
                return
