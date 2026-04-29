"""Evaluation lifecycle helper emitters built on top of OpenTrap run events."""

from __future__ import annotations

from opentrap.events import EventSink, emit_event


def emit_evaluation_phase(
    event_sink: EventSink | None,
    phase: str,
    *,
    detail: str | None = None,
) -> None:
    """Emit one structured evaluation phase event when a sink is available."""
    if event_sink is None:
        return
    emit_event(
        event_sink,
        "evaluate_phase",
        phase=phase,
        detail=detail,
    )


def emit_evaluation_progress(
    event_sink: EventSink | None,
    *,
    processed: int,
    total: int,
) -> None:
    """Emit one structured evaluation progress event when a sink is available."""
    if event_sink is None or total <= 0:
        return
    emit_event(
        event_sink,
        "evaluate_progress",
        processed=max(0, min(processed, total)),
        total=total,
    )
