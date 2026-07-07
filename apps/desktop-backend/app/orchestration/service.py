"""Scheduler-facing desired-state reconciliation with owned lifecycle tasks."""

from __future__ import annotations

import asyncio
import inspect
import logging
from math import isfinite

from .errors import FailureCategory, OrchestrationError, OrchestrationFailure
from .manager import CameraWorkerManager
from .models import OrchestrationHealth
from .protocols import CameraCatalog, Scheduler
from .security import sanitize_error

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
        if (
            not isfinite(reconcile_interval_seconds)
            or reconcile_interval_seconds < 0.05
            or reconcile_interval_seconds > 86_400
        ):
            raise ValueError(
                "reconcile interval must be finite and between 0.05 and 86400 seconds"
            )
        self._manager = manager
        self._catalog = catalog
        self._scheduler = scheduler
        self._interval = reconcile_interval_seconds
        self._state_lock = asyncio.Lock()
        self._reconcile_lock = asyncio.Lock()
        self._reconcile_tasks: set[asyncio.Task[object]] = set()
        self._accepting_reconcile = False
        self._scheduler_registered = False
        self._started = False
        self._start_task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._last_failure: OrchestrationFailure | None = None

    async def start(self) -> None:
        async with self._state_lock:
            if self._stop_task is not None:
                raise OrchestrationError(
                    FailureCategory.SHUTDOWN, None, "orchestration service is stopping"
                )
            if self._started:
                return
            if self._start_task is None:
                self._start_task = asyncio.create_task(
                    self._start_impl(), name="camera-service-start"
                )
            start_task = self._start_task
        try:
            await asyncio.shield(start_task)
        except asyncio.CancelledError:
            cleanup_task = asyncio.create_task(
                self.stop(), name="camera-service-start-cancel-cleanup"
            )
            try:
                await _settle_task(cleanup_task)
            except BaseException as cleanup_error:
                LOGGER.error(
                    "cancelled service startup cleanup failed: %s",
                    sanitize_error(cleanup_error),
                )
            raise

    async def _start_impl(self) -> None:
        async with self._state_lock:
            self._accepting_reconcile = True
        try:
            await self.reconcile()
            async with self._state_lock:
                if not self._accepting_reconcile:
                    return
            if self._scheduler is not None:
                try:
                    self._scheduler.add_job(
                        self.reconcile,
                        "interval",
                        id=self.JOB_ID,
                        seconds=self._interval,
                        coalesce=True,
                        max_instances=1,
                        replace_existing=True,
                    )
                except Exception as error:
                    raise OrchestrationError(
                        FailureCategory.SCHEDULER,
                        None,
                        error,
                        cause=error,
                    ) from None
                self._scheduler_registered = True
            async with self._state_lock:
                if self._accepting_reconcile and self._stop_task is None:
                    self._started = True
        except BaseException as error:
            async with self._state_lock:
                self._accepting_reconcile = False
            try:
                await self._manager.shutdown()
            except Exception as cleanup_error:
                LOGGER.error(
                    "service startup rollback failed: %s", sanitize_error(cleanup_error)
                )
            if isinstance(error, OrchestrationError):
                self._last_failure = error.failure
                raise
            if isinstance(error, asyncio.CancelledError):
                raise
            public = OrchestrationError(
                FailureCategory.INTERNAL, None, error, cause=error
            )
            self._last_failure = public.failure
            raise public from None

    async def reconcile(self) -> None:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("reconciliation requires an asyncio task")
        async with self._state_lock:
            if not self._accepting_reconcile:
                return
            self._reconcile_tasks.add(current)
        try:
            if self._reconcile_lock.locked():
                LOGGER.info(
                    "camera reconciliation already running; overlapping invocation skipped"
                )
                return
            async with self._reconcile_lock:
                async with self._state_lock:
                    if not self._accepting_reconcile:
                        return
                desired = list(await self._catalog.list_enabled_cameras())
                async with self._state_lock:
                    if not self._accepting_reconcile:
                        return
                await self._manager.reconcile(desired)
        except asyncio.CancelledError:
            raise
        except OrchestrationError as error:
            self._last_failure = error.failure
            LOGGER.error(
                "camera reconciliation failed [%s]: %s",
                error.category.value,
                error.public_message,
            )
            raise
        except Exception as error:
            public = OrchestrationError(
                FailureCategory.SCHEDULER, None, error, cause=error
            )
            self._last_failure = public.failure
            LOGGER.error("camera reconciliation failed: %s", public.public_message)
            raise public from None
        finally:
            async with self._state_lock:
                self._reconcile_tasks.discard(current)

    async def stop(self) -> None:
        async with self._state_lock:
            if self._stop_task is None:
                self._stop_task = asyncio.create_task(
                    self._stop_impl(), name="camera-service-stop"
                )
            stop_task = self._stop_task
        await asyncio.shield(stop_task)

    async def _stop_impl(self) -> None:
        failures: list[OrchestrationFailure] = []
        async with self._state_lock:
            self._accepting_reconcile = False
            start_task = (
                self._start_task
                if self._start_task is not asyncio.current_task()
                and self._start_task is not None
                else None
            )

        if start_task is not None:
            # The start task owns rollback and records its categorized failure.
            # Gathering it here observes every terminal result before scheduler
            # or manager cleanup can race ahead of late startup work.
            await asyncio.gather(start_task, return_exceptions=True)

        async with self._state_lock:
            reconcile_tasks = tuple(
                task
                for task in self._reconcile_tasks
                if task is not asyncio.current_task()
            )

        if self._scheduler is not None and self._scheduler_registered:
            try:
                result = self._scheduler.remove_job(self.JOB_ID)
                if inspect.isawaitable(result):
                    await result
            except Exception as error:
                public = OrchestrationError(
                    FailureCategory.SCHEDULER, None, error, cause=error
                )
                failures.append(public.failure)
                LOGGER.error(
                    "failed to remove camera reconciliation job: %s",
                    public.public_message,
                )
            finally:
                self._scheduler_registered = False

        if reconcile_tasks:
            results = await asyncio.gather(*reconcile_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    public = OrchestrationError(
                        FailureCategory.SCHEDULER, None, result, cause=result
                    )
                    failures.append(public.failure)

        try:
            await self._manager.shutdown()
        except Exception as error:
            public = OrchestrationError(
                FailureCategory.SHUTDOWN, None, error, cause=error
            )
            failures.append(public.failure)

        async with self._state_lock:
            self._started = False
        if failures:
            public = OrchestrationError(
                FailureCategory.SHUTDOWN,
                None,
                f"service shutdown completed with {len(failures)} failure(s)",
            )
            self._last_failure = public.failure
            raise public

    def health(self) -> OrchestrationHealth:
        return self._manager.health()

    def runtime_status(self) -> dict[str, object]:
        """Return a non-blocking, secret-free scheduler/service snapshot."""
        return {
            "running": self._started and self._stop_task is None,
            "accepting_reconcile": self._accepting_reconcile,
            "scheduler_registered": self._scheduler_registered,
            "reconcile_in_progress": bool(self._reconcile_tasks),
            "last_failure": self._last_failure.to_dict() if self._last_failure else None,
        }


async def _settle_task(task: asyncio.Task[object]) -> None:
    """Wait for owned cleanup despite repeated cancellation of this waiter."""
    while True:
        try:
            await asyncio.shield(task)
            return
        except asyncio.CancelledError:
            if task.done():
                task.result()
                return
