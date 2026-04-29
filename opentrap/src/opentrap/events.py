"""Typed run/evaluation lifecycle events used by CLI renderers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

RunEventType = Literal[
    "run_started",
    "generate_started",
    "generate_progress",
    "generate_completed",
    "adapter_launching",
    "adapter_ready",
    "case_started",
    "case_finished",
    "evaluate_started",
    "evaluate_phase",
    "evaluate_progress",
    "evaluate_completed",
    "run_finalized",
    "run_failed",
    "adapter_status_update",
]


@dataclass(frozen=True)
class RunEvent:
    """One structured lifecycle/progress event emitted during trap runs."""

    type: RunEventType
    payload: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[RunEvent], None]


def emit_event(sink: EventSink, event_type: RunEventType, **payload: Any) -> None:
    sink(RunEvent(type=event_type, payload=dict(payload)))
