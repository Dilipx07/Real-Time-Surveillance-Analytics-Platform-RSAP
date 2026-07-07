"""Camera lifecycle and desktop analytics orchestration."""

from .manager import CameraWorkerManager
from .errors import (
    FailureCategory,
    OrchestrationError,
    OrchestrationFailure,
    ShutdownError,
)
from .models import (
    CameraDefinition,
    CameraHealth,
    CameraMetrics,
    CameraStatus,
    FrozenPayload,
    LifecycleOperation,
    LifecycleOperationResult,
    LifecycleOutcome,
    OrchestrationHealth,
    RoutedAnalyticsEvent,
    WorkerState,
)
from .protocols import CameraCatalog, EventSink, Scheduler
from .service import CameraOrchestrationService

__all__ = [
    "CameraCatalog",
    "CameraDefinition",
    "CameraHealth",
    "CameraMetrics",
    "CameraOrchestrationService",
    "CameraStatus",
    "CameraWorkerManager",
    "EventSink",
    "FailureCategory",
    "FrozenPayload",
    "LifecycleOperation",
    "LifecycleOperationResult",
    "LifecycleOutcome",
    "OrchestrationError",
    "OrchestrationFailure",
    "OrchestrationHealth",
    "RoutedAnalyticsEvent",
    "Scheduler",
    "ShutdownError",
    "WorkerState",
]
