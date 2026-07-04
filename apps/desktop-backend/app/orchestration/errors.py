"""Stable, credential-safe orchestration failures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .security import sanitize_error


class FailureCategory(StrEnum):
    CONFIGURATION = "configuration"
    CAPACITY = "capacity"
    CAPTURE = "capture"
    MODEL = "model"
    PIPELINE = "pipeline"
    CALLBACK = "callback"
    SINK = "sink"
    SCHEDULER = "scheduler"
    SHUTDOWN = "shutdown"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class OrchestrationFailure:
    category: FailureCategory
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {"category": self.category.value, "summary": self.summary}


class OrchestrationError(RuntimeError):
    """Public exception that never exposes the underlying credential-bearing error."""

    def __init__(
        self,
        category: FailureCategory,
        camera_id: str | None,
        message: BaseException | str,
        *,
        cause: BaseException | None = None,
    ) -> None:
        self.category = category
        self.camera_id = camera_id
        self.public_message = sanitize_error(message)
        self._internal_cause = cause
        prefix = f"camera {camera_id}: " if camera_id else ""
        super().__init__(f"{prefix}{self.public_message}")

    @property
    def failure(self) -> OrchestrationFailure:
        return OrchestrationFailure(self.category, self.public_message)


class ShutdownError(OrchestrationError):
    def __init__(self, failures: tuple[OrchestrationFailure, ...]) -> None:
        self.failures = failures
        super().__init__(
            FailureCategory.SHUTDOWN,
            None,
            f"orchestration shutdown completed with {len(failures)} failure(s)",
        )
