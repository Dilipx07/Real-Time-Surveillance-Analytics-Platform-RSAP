"""Scheduler-facing desired-state reconciliation service."""

from __future__ import annotations

import asyncio
import logging

from .manager import CameraWorkerManager
from .models import OrchestrationHealth
from .protocols import CameraCatalog, Scheduler

LOGGER = logging.getLogger(__name__)


class CameraOrchestrationService:
    JOB_ID = "desktop-camera-reconcile"

    def __init__(
        self,
        manager: CameraWorkerManager,
        catalog: CameraCatalog,
        *,
        scheduler: Scheduler | None = None,
        reconcile_interval_seconds: float = 30.0,
    ) -> None:
        if reconcile_interval_seconds <= 0:
            raise ValueError("reconcile interval must be positive")
        self._manager = manager
        self._catalog = catalog
        self._scheduler = scheduler
        self._interval = reconcile_interval_seconds
        self._reconcile_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.reconcile()
        if self._scheduler is not None:
            self._scheduler.add_job(
                self.reconcile,
                "interval",
                id=self.JOB_ID,
                seconds=self._interval,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
        self._started = True

    async def reconcile(self) -> None:
        if self._reconcile_lock.locked():
            LOGGER.info(
                "camera reconciliation already running; overlapping invocation skipped"
            )
            return
        async with self._reconcile_lock:
            desired = list(await self._catalog.list_enabled_cameras())
            await self._manager.reconcile(desired)

    async def stop(self) -> None:
        if self._scheduler is not None and self._started:
            try:
                self._scheduler.remove_job(self.JOB_ID)
            except Exception:
                LOGGER.exception("failed to remove camera reconciliation job")
        self._started = False
        await self._manager.shutdown()

    def health(self) -> OrchestrationHealth:
        return self._manager.health()
