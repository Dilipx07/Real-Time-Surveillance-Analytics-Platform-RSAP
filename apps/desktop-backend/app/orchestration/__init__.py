"""Camera lifecycle and desktop analytics orchestration."""

from .manager import CameraWorkerManager
from .models import (
    CameraDefinition,
    CameraMetrics,
    CameraStatus,
    OrchestrationHealth,
    WorkerState,
)
from .protocols import CameraCatalog, EventSink, Scheduler
from .service import CameraOrchestrationService

__all__ = [
    "CameraCatalog",
    "CameraDefinition",
    "CameraMetrics",
    "CameraOrchestrationService",
    "CameraStatus",
    "CameraWorkerManager",
    "EventSink",
    "OrchestrationHealth",
    "Scheduler",
    "WorkerState",
]
