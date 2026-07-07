from __future__ import annotations

import asyncio
import gc
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from app.orchestration import FailureCategory, OrchestrationError, RoutedAnalyticsEvent
from app.orchestration.adapters import (
    Agent2CameraCatalog,
    Agent2EventSink,
    AsyncioPeriodicScheduler,
)
from app.schemas import CameraCreate, CameraUpdate, LocalSession
from main import create_app
from tests.fakes import CaptureFactory, FakeCapture, PipelineFactory


def _session(
    *, role: str = "va_user", max_cameras: int = 8
) -> tuple[LocalSession, datetime]:
    expiry = datetime.now(UTC) + timedelta(hours=1)
    return (
        LocalSession(
            access_token="jwt",
            session_token="session",
            refresh_token="refresh",
            access_expires_at=datetime.now(UTC) + timedelta(minutes=15),
            user={"id": "user-1", "role": role, "permissions": []},
            license={
                "valid_until": expiry.isoformat(),
                "is_active": True,
                "max_cameras": max_cameras,
                "analytics_modules": [
                    "intrusion_detection",
                    "people_counting",
                    "zone_analytics",
                ],
            },
        ),
        expiry,
    )


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer jwt", "X-Session-Token": "session"}


class _BlockingCaptureFactory:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.instances: list[FakeCapture] = []

    def __call__(self, definition: object) -> FakeCapture:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("blocked capture factory was not released")
        capture = FakeCapture()
        self.instances.append(capture)
        return capture

    async def wait_until_entered(self) -> None:
        assert await asyncio.to_thread(self.entered.wait, 2)


async def _seed_startup_camera(container: Container) -> None:
    await container.database.migrate()
    session, expiry = _session()
    await container.sessions.save(session, expiry)
    await container.cameras.create(
        CameraCreate(name="Blocked", stream_url="0", stream_type="webcam"), 8
    )


def _pending_owned_tasks() -> list[str]:
    prefixes = (
        "camera-",
        "scheduler-",
        "desktop-startup-",
        "desktop-lifespan-startup-",
    )
    return [
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith(prefixes)
    ]


@pytest.mark.asyncio
async def test_catalog_adapter_maps_only_active_authorized_cameras(settings) -> None:
    container = Container(settings)
    await container.start()
    session, expiry = _session()
    await container.sessions.save(session, expiry)
    active = await container.cameras.create(
        CameraCreate(
            name="Active webcam",
            stream_url="0",
            stream_type="webcam",
            analytics_config={"analytics_fps": 5.0, "people_counting": True},
        ),
        8,
    )
    inactive = await container.cameras.create(
        CameraCreate(name="Inactive", stream_url="1", stream_type="webcam"), 8
    )
    await container.cameras.update(inactive["id"], CameraUpdate(is_active=False))

    definitions = await container.camera_catalog.list_enabled_cameras()

    assert isinstance(container.camera_catalog, Agent2CameraCatalog)
    assert [definition.camera_id for definition in definitions] == [active["id"]]
    assert definitions[0].source == 0
    assert definitions[0].cv_config.analytics_fps == 5.0
    assert "source" not in repr(definitions[0])
    await container.close()


@pytest.mark.asyncio
async def test_catalog_adapter_classifies_invalid_runtime_configuration(settings) -> None:
    container = Container(settings)
    await container.start()
    session, expiry = _session()
    await container.sessions.save(session, expiry)
    camera = await container.cameras.create(
        CameraCreate(
            name="Invalid zone",
            stream_url="0",
            stream_type="webcam",
            zones=[{"id": "missing-vertices"}],
        ),
        8,
    )

    with pytest.raises(OrchestrationError) as caught:
        await container.camera_catalog.list_enabled_cameras()

    assert caught.value.category is FailureCategory.CONFIGURATION
    assert caught.value.camera_id == camera["id"]
    assert "vertices" not in str(caught.value)
    await container.close()


@pytest.mark.asyncio
async def test_catalog_adapter_enforces_license_limit_for_manual_control(settings) -> None:
    container = Container(settings)
    await container.start()
    session, expiry = _session(max_cameras=1)
    await container.sessions.save(session, expiry)
    first = await container.cameras.create(
        CameraCreate(name="A", stream_url="0", stream_type="webcam"), 8
    )
    second = await container.cameras.create(
        CameraCreate(name="B", stream_url="1", stream_type="webcam"), 8
    )

    assert await container.camera_catalog.definition_for(first["id"], session)
    assert await container.camera_catalog.definition_for(second["id"], session) is None
    await container.close()


@pytest.mark.asyncio
async def test_event_sink_adapter_persists_through_agent2_analytics_path(settings) -> None:
    container = Container(settings)
    await container.start()
    session, expiry = _session()
    await container.sessions.save(session, expiry)
    camera = await container.cameras.create(
        CameraCreate(name="Gate", stream_url="0", stream_type="webcam"), 8
    )
    callback = SimpleNamespace(
        event_type="intrusion",
        timestamp=datetime.now(UTC),
        track_id=7,
        zone_id="front",
        payload={"confidence": 0.9},
    )
    event = RoutedAnalyticsEvent.from_callback(camera["id"], 2, callback)

    assert isinstance(container.event_sink, Agent2EventSink)
    await container.event_sink.emit(event)
    persisted = await container.analytics.list_events(10, 0)

    assert persisted["total"] == 1
    assert persisted["items"][0]["camera_id"] == camera["id"]
    assert persisted["items"][0]["payload"] == {
        "accepted_at": event.accepted_at.isoformat(),
        "confidence": 0.9,
        "generation": 2,
        "track_id": 7,
        "zone_id": "front",
    }
    assert await container.queue.count() == 2
    await container.close()


@pytest.mark.asyncio
async def test_event_sink_adapter_sanitizes_persistence_failure(settings) -> None:
    container = Container(settings)
    await container.start()
    session, expiry = _session()
    await container.sessions.save(session, expiry)
    event = RoutedAnalyticsEvent.from_callback(
        str(UUID(int=1)),
        1,
        SimpleNamespace(
            event_type="intrusion",
            timestamp=datetime.now(UTC),
            track_id=None,
            zone_id=None,
            payload={},
        ),
    )

    with pytest.raises(OrchestrationError) as caught:
        await container.event_sink.emit(event)

    assert caught.value.category is FailureCategory.SINK
    assert "camera does not exist" not in str(caught.value)
    assert "analytics event persistence failed" in str(caught.value)
    await container.close()


@pytest.mark.asyncio
async def test_scheduler_adapter_is_non_overlapping_and_shutdown_awaits_tasks() -> None:
    scheduler = AsyncioPeriodicScheduler()
    gate = asyncio.Event()
    calls = 0

    async def job() -> None:
        nonlocal calls
        calls += 1
        await gate.wait()

    scheduler.add_job(job, "interval", id="job", seconds=0.05)
    await asyncio.sleep(0.08)
    assert calls == 1
    assert scheduler.job_count == 1
    gate.set()
    await scheduler.shutdown()
    after_shutdown = calls
    await asyncio.sleep(0.06)
    assert calls == after_shutdown
    assert scheduler.job_count == 0


class _RegistrationFailureScheduler(AsyncioPeriodicScheduler):
    def add_job(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("scheduler registration failed with token=hidden")


@pytest.mark.asyncio
async def test_production_orchestration_startup_rollback_leaves_no_worker_tasks(
    settings,
) -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory()
    container = Container(
        settings,
        capture_factory=captures,
        pipeline_factory=pipelines,
        scheduler=_RegistrationFailureScheduler(),
    )
    await container.database.migrate()
    session, expiry = _session()
    await container.sessions.save(session, expiry)
    await container.cameras.create(
        CameraCreate(name="Gate", stream_url="0", stream_type="webcam"), 8
    )

    with pytest.raises(OrchestrationError) as caught:
        await container.start()

    assert caught.value.category is FailureCategory.SCHEDULER
    assert container.camera_manager.active_camera_ids() == ()
    assert captures.instances and captures.instances[0].close_calls == 1
    assert pipelines.instances and pipelines.instances[0].aclose_calls == 1
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith(("camera-", "scheduler-"))
    ]
    await container.close()


@pytest.mark.asyncio
async def test_production_orchestration_shutdown_no_worker_tasks(settings) -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory()
    container = Container(
        settings, capture_factory=captures, pipeline_factory=pipelines
    )
    await container.start()
    session, expiry = _session()
    await container.sessions.save(session, expiry)
    await container.cameras.create(
        CameraCreate(name="Gate", stream_url="0", stream_type="webcam"), 8
    )
    await container.orchestration.reconcile()
    assert len(container.camera_manager.active_camera_ids()) == 1

    await container.close()

    assert container.camera_manager.active_camera_ids() == ()
    assert captures.instances[0].close_calls == 1
    assert pipelines.instances[0].aclose_calls == 1
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith(("camera-", "scheduler-"))
    ]


@pytest.mark.asyncio
async def test_container_start_cancellation_closes_all_resources(settings) -> None:
    captures = _BlockingCaptureFactory()
    pipelines = PipelineFactory()
    container = Container(
        settings, capture_factory=captures, pipeline_factory=pipelines
    )
    await _seed_startup_camera(container)
    startup = asyncio.create_task(container.start(), name="test-container-start")
    await captures.wait_until_entered()

    startup.cancel()
    async with asyncio.timeout(1):
        while container.orchestration._stop_task is None:
            await asyncio.sleep(0)
    startup.cancel()
    captures.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(startup, timeout=2)

    assert container._close_task is not None and container._close_task.done()
    assert container.central._client.is_closed
    assert container.orchestration._start_task is not None
    assert container.orchestration._start_task.done()
    assert container.camera_manager.active_camera_ids() == ()
    assert container.orchestration_scheduler.job_count == 0
    assert container.orchestration_scheduler._closed is True
    assert captures.instances[0].close_calls == 1
    assert pipelines.instances[0].aclose_calls == 1
    assert _pending_owned_tasks() == []


@pytest.mark.asyncio
async def test_service_stop_during_start_observes_start_task(
    settings,
) -> None:
    captures = _BlockingCaptureFactory()
    pipelines = PipelineFactory()
    container = Container(
        settings, capture_factory=captures, pipeline_factory=pipelines
    )
    await _seed_startup_camera(container)
    startup = asyncio.create_task(
        container.orchestration.start(), name="test-service-start"
    )
    await captures.wait_until_entered()

    stopping = asyncio.create_task(
        container.orchestration.stop(), name="test-service-stop"
    )
    captures.release.set()
    await asyncio.wait_for(asyncio.gather(startup, stopping), timeout=2)

    assert container.orchestration._start_task is not None
    assert container.orchestration._start_task.done()
    assert container.orchestration.runtime_status()["running"] is False
    assert container.camera_manager.active_camera_ids() == ()
    assert container.orchestration_scheduler.job_count == 0
    assert _pending_owned_tasks() == []
    await container.close()


@pytest.mark.asyncio
async def test_lifespan_start_cancellation_closes_all_resources(settings) -> None:
    captures = _BlockingCaptureFactory()
    pipelines = PipelineFactory()
    container = Container(
        settings, capture_factory=captures, pipeline_factory=pipelines
    )
    await _seed_startup_camera(container)
    app = create_app(settings, container_factory=lambda _: container)
    lifespan = app.router.lifespan_context(app)
    startup = asyncio.create_task(lifespan.__aenter__(), name="test-lifespan-start")
    await captures.wait_until_entered()

    startup.cancel()
    captures.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(startup, timeout=2)

    assert container._close_task is not None and container._close_task.done()
    assert container.central._client.is_closed
    assert container.orchestration._start_task is not None
    assert container.orchestration._start_task.done()
    assert container.camera_manager.active_camera_ids() == ()
    assert container.orchestration_scheduler._closed is True
    assert _pending_owned_tasks() == []


@pytest.mark.asyncio
async def test_cancelled_start_does_not_log_unretrieved_task_exception(
    settings,
) -> None:
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    try:
        captures = _BlockingCaptureFactory()
        container = Container(
            settings,
            capture_factory=captures,
            pipeline_factory=PipelineFactory(),
        )
        await _seed_startup_camera(container)
        startup = asyncio.create_task(container.start(), name="test-observed-start")
        await captures.wait_until_entered()
        startup.cancel()
        captures.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(startup, timeout=2)
        assert container.orchestration._start_task is not None
        assert container.orchestration._start_task.done()
        del startup, container
        gc.collect()
        await asyncio.sleep(0)
        assert not [
            context
            for context in contexts
            if "Task exception was never retrieved" in str(context.get("message"))
        ]
    finally:
        loop.set_exception_handler(previous_handler)


def test_container_lifespan_and_authenticated_orchestration_routes(settings) -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory()
    made: list[Container] = []

    def factory(resolved_settings):
        container = Container(
            resolved_settings,
            capture_factory=captures,
            pipeline_factory=pipelines,
        )
        made.append(container)
        return container

    app = create_app(settings, container_factory=factory)
    with TestClient(app, raise_server_exceptions=False) as client:
        container = made[0]
        assert container.orchestration.runtime_status()["running"] is True
        assert container.orchestration_scheduler.job_count == 1

        for path in (
            "/orchestration/health",
            "/orchestration/cameras",
            f"/orchestration/cameras/{UUID(int=1)}/status",
        ):
            assert client.get(path).status_code == 401
        for operation in ("start", "stop", "restart"):
            response = client.post(
                f"/orchestration/cameras/{UUID(int=1)}/{operation}"
            )
            assert response.status_code == 401

        session, expiry = _session()
        client.portal.call(container.sessions.save, session, expiry)
        created = client.post(
            "/cameras",
            headers=_headers(),
            json={
                "name": "Gate",
                "stream_url": "rtsp://operator:secret@camera/live",
                "stream_type": "rtsp",
            },
        )
        camera_id = created.json()["data"]["id"]

        started = client.post(
            f"/orchestration/cameras/{camera_id}/start", headers=_headers()
        )
        assert started.status_code == 200
        assert started.json()["data"]["outcome"] == "started"
        status = client.get(
            f"/orchestration/cameras/{camera_id}/status", headers=_headers()
        )
        assert status.status_code == 200
        assert status.json()["data"]["camera_id"] == camera_id
        assert "secret" not in status.text
        assert "stream_url" not in status.text
        restarted = client.post(
            f"/orchestration/cameras/{camera_id}/restart", headers=_headers()
        )
        assert restarted.json()["data"]["outcome"] == "restarted"
        stopped = client.post(
            f"/orchestration/cameras/{camera_id}/stop", headers=_headers()
        )
        assert stopped.json()["data"]["outcome"] == "stopped"
        health = client.get("/orchestration/health", headers=_headers())
        assert health.status_code == 200
        assert health.json()["data"]["service"]["running"] is True
        assert client.post(
            f"/orchestration/cameras/{UUID(int=2)}/start", headers=_headers()
        ).status_code == 404

    assert container.orchestration.runtime_status()["running"] is False
    assert container.camera_manager.active_camera_ids() == ()
    assert container.orchestration_scheduler.job_count == 0


def test_orchestration_permission_denies_control_but_allows_read(settings) -> None:
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        container = app.state.container
        session, expiry = _session(role="unknown")
        session.user["permissions"] = [
            {"resource": "cameras", "actions": ["read"]}
        ]
        client.portal.call(container.sessions.save, session, expiry)
        assert client.get("/orchestration/health", headers=_headers()).status_code == 200
        assert client.post(
            f"/orchestration/cameras/{UUID(int=1)}/start", headers=_headers()
        ).status_code == 403
