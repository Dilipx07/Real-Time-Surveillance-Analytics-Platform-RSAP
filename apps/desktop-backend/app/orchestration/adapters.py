"""Production adapters between Agent-2 services and Agent-3 protocols."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC
from math import isfinite
from typing import Any
from uuid import UUID

from cv_engine import CVConfig
from cv_engine.types import CountingLine, Zone

from app.authorization import AuthorizationError, AuthorizationService
from app.repositories import CameraRepository, SessionRepository
from app.schemas import AnalyticsEventCreate, LocalSession
from app.services import AnalyticsService

from .errors import FailureCategory, OrchestrationError
from .models import CameraDefinition, RoutedAnalyticsEvent
from .security import sanitize_error

LOGGER = logging.getLogger(__name__)

_CV_SCALAR_FIELDS = frozenset(
    {
        "analytics_fps",
        "confidence_threshold",
        "device",
        "face_interval",
        "face_recognition",
        "face_tolerance",
        "intrusion_cooldown_seconds",
        "intrusion_retention_margin_seconds",
        "iou_threshold",
        "people_counter_stale_after_frames",
        "target_class_ids",
        "tracker_iou_threshold",
        "tracker_max_age",
        "tracker_min_hits",
    }
)
_CV_FLOAT_FIELDS = frozenset(
    {
        "analytics_fps",
        "confidence_threshold",
        "face_tolerance",
        "intrusion_cooldown_seconds",
        "intrusion_retention_margin_seconds",
        "iou_threshold",
        "tracker_iou_threshold",
    }
)
_CV_INTEGER_FIELDS = frozenset(
    {
        "face_interval",
        "people_counter_stale_after_frames",
        "tracker_max_age",
        "tracker_min_hits",
    }
)


class Agent2CameraCatalog:
    """Expose authorized active camera records as secret-safe definitions."""

    def __init__(
        self,
        cameras: CameraRepository,
        sessions: SessionRepository,
        authorization: AuthorizationService,
    ) -> None:
        self._cameras = cameras
        self._sessions = sessions
        self._authorization = authorization

    async def list_enabled_cameras(self) -> Sequence[CameraDefinition]:
        session = await self._background_session()
        if session is None:
            return ()
        limit = self._authorization.max_cameras(session)
        records = await self._cameras.list_active(limit)
        return tuple(self._definition(record) for record in records)

    async def definition_for(
        self,
        camera_id: UUID | str,
        session: LocalSession,
        *,
        require_active: bool = True,
    ) -> CameraDefinition | None:
        self._authorization.require(session, "camera.read")
        canonical_id = str(UUID(str(camera_id)))
        if require_active:
            limit = self._authorization.max_cameras(session)
            records = await self._cameras.list_active(limit)
            record = next(
                (item for item in records if str(item["id"]) == canonical_id), None
            )
        else:
            record = await self._cameras.get(canonical_id)
        if record is None:
            return None
        return self._definition(record)

    async def exists(self, camera_id: UUID | str, session: LocalSession) -> bool:
        self._authorization.require(session, "camera.read")
        return await self._cameras.get(camera_id) is not None

    async def _background_session(self) -> LocalSession | None:
        record = await self._sessions.get_record()
        if record is None or record.status != "active":
            return None
        try:
            self._authorization.require(record.session, "camera.read")
        except AuthorizationError:
            return None
        return record.session

    @staticmethod
    def _definition(record: dict[str, Any]) -> CameraDefinition:
        try:
            camera_id = str(UUID(str(record["id"])))
            source_value = record["stream_url"]
            if not isinstance(source_value, str) or not source_value.strip():
                raise ValueError("camera source is unavailable")
            stream_type = record.get("stream_type")
            source: str | int = (
                int(source_value)
                if stream_type == "webcam" and source_value.isdecimal()
                else source_value
            )
            cv_config = _cv_config(record)
            return CameraDefinition(camera_id=camera_id, source=source, cv_config=cv_config)
        except OrchestrationError:
            raise
        except Exception as error:
            camera_id = _safe_camera_id(record.get("id"))
            raise OrchestrationError(
                FailureCategory.CONFIGURATION,
                camera_id,
                "camera runtime configuration is invalid",
                cause=error,
            ) from None


class Agent2EventSink:
    """Persist orchestration events through Agent-2 authorization and sync."""

    def __init__(
        self,
        analytics: AnalyticsService,
        sessions: SessionRepository,
    ) -> None:
        self._analytics = analytics
        self._sessions = sessions

    async def emit(self, event: RoutedAnalyticsEvent) -> None:
        record = await self._sessions.get_record()
        if record is None or record.status != "active":
            raise OrchestrationError(
                FailureCategory.SINK,
                event.camera_id,
                "analytics persistence requires an active local session",
            )
        try:
            payload = event.payload.to_dict()
            payload.update(
                {
                    "generation": event.generation,
                    "track_id": event.track_id,
                    "zone_id": event.zone_id,
                    "accepted_at": event.accepted_at.isoformat(),
                }
            )
            await self._analytics.event(
                record.session,
                AnalyticsEventCreate(
                    camera_id=UUID(event.camera_id),
                    event_type=event.event_type,
                    payload=payload,
                    created_at=event.timestamp.astimezone(UTC),
                ),
            )
        except OrchestrationError:
            raise
        except Exception as error:
            raise OrchestrationError(
                FailureCategory.SINK,
                event.camera_id,
                "analytics event persistence failed",
                cause=error,
            ) from None


@dataclass(slots=True)
class _PeriodicJob:
    job_id: str
    callback: Callable[[], Awaitable[None]]
    seconds: float
    task: asyncio.Task[None]


class AsyncioPeriodicScheduler:
    """One-task-per-job periodic scheduler with awaited cancellation."""

    def __init__(self) -> None:
        self._jobs: dict[str, _PeriodicJob] = {}
        self._closed = False
        self._last_error: str | None = None

    @property
    def job_count(self) -> int:
        return len(self._jobs)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def add_job(
        self,
        func: Callable[[], Awaitable[None]],
        trigger: str,
        **kwargs: Any,
    ) -> _PeriodicJob:
        if self._closed:
            raise RuntimeError("scheduler is closed")
        if trigger != "interval":
            raise ValueError("only interval scheduling is supported")
        job_id = str(kwargs.get("id", "")).strip()
        seconds = float(kwargs.get("seconds", 0))
        if not job_id or seconds < 0.05 or seconds > 86_400:
            raise ValueError("scheduled job requires an ID and a bounded interval")
        if job_id in self._jobs and not kwargs.get("replace_existing", False):
            raise ValueError("scheduled job already exists")
        if job_id in self._jobs:
            raise ValueError("replace_existing requires awaiting remove_job first")
        task = asyncio.create_task(
            self._run(job_id, func, seconds), name=f"scheduler-{job_id}"
        )
        job = _PeriodicJob(job_id, func, seconds, task)
        self._jobs[job_id] = job
        return job

    async def remove_job(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is None:
            return
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        jobs = tuple(self._jobs)
        await asyncio.gather(*(self.remove_job(job_id) for job_id in jobs))

    async def _run(
        self,
        job_id: str,
        callback: Callable[[], Awaitable[None]],
        seconds: float,
    ) -> None:
        try:
            while True:
                await asyncio.sleep(seconds)
                try:
                    result = callback()
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._last_error = sanitize_error(error)
                    LOGGER.error(
                        "scheduled job %s failed: %s", job_id, self._last_error
                    )
        except asyncio.CancelledError:
            raise


def _cv_config(record: dict[str, Any]) -> CVConfig:
    raw = record.get("analytics_config") or {}
    if not isinstance(raw, dict):
        raise ValueError("analytics configuration must be an object")
    values = {key: raw[key] for key in _CV_SCALAR_FIELDS if key in raw}
    for key in _CV_FLOAT_FIELDS & values.keys():
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        if not isfinite(float(value)):
            raise ValueError(f"{key} must be finite")
    for key in _CV_INTEGER_FIELDS & values.keys():
        if type(values[key]) is not int:
            raise ValueError(f"{key} must be an integer")
    if "face_recognition" in values and type(values["face_recognition"]) is not bool:
        raise ValueError("face_recognition must be a boolean")
    if "device" in values and not isinstance(values["device"], str):
        raise ValueError("device must be a string")
    if "target_class_ids" in values:
        class_ids = values["target_class_ids"]
        if not isinstance(class_ids, (list, tuple)) or any(
            type(item) is not int for item in class_ids
        ):
            raise ValueError("target_class_ids must contain integers")
        values["target_class_ids"] = tuple(class_ids)

    zones = tuple(_zone(value) for value in record.get("zones") or ())
    if zones:
        values["zones"] = zones
        values["intrusion_zone_ids"] = frozenset(
            zone.id for zone in zones if zone.alert_on_entry
        )
    counting_line = raw.get("counting_line")
    if counting_line is not None:
        if not isinstance(counting_line, dict):
            raise ValueError("counting_line must be an object")
        normalized = counting_line.get("normalized", True)
        if type(normalized) is not bool:
            raise ValueError("counting_line.normalized must be a boolean")
        values["counting_line"] = CountingLine(
            start=tuple(counting_line["start"]),
            end=tuple(counting_line["end"]),
            normalized=normalized,
        )
    return CVConfig(**values)


def _zone(value: Any) -> Zone:
    if not isinstance(value, dict):
        raise ValueError("zone must be an object")
    normalized = value.get("normalized", True)
    alert_on_entry = value.get("alert_on_entry", False)
    color = value.get("color", (0, 255, 0))
    if type(normalized) is not bool or type(alert_on_entry) is not bool:
        raise ValueError("zone flags must be booleans")
    if (
        not isinstance(color, (list, tuple))
        or len(color) != 3
        or any(type(channel) is not int or not 0 <= channel <= 255 for channel in color)
    ):
        raise ValueError("zone color must contain three byte values")
    return Zone(
        id=str(value["id"]),
        name=str(value.get("name") or value["id"]),
        vertices=tuple(tuple(point) for point in value["vertices"]),
        color=tuple(color),
        alert_on_entry=alert_on_entry,
        normalized=normalized,
    )


def _safe_camera_id(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None
