from __future__ import annotations

from opentrap.events import RunEvent
from opentrap.run_orchestration import _forward_adapter_stderr_line


def test_forward_adapter_stderr_line_emits_status_event_for_adapter_prefix() -> None:
    events: list[RunEvent] = []

    handled = _forward_adapter_stderr_line(
        "[adapter] Host starting on 127.0.0.1:7860\n",
        event_sink=events.append,
    )

    assert handled is True
    assert events == [
        RunEvent(
            type="adapter_status_update",
            payload={"message": "Host starting on 127.0.0.1:7860"},
        )
    ]


def test_forward_adapter_stderr_line_ignores_non_adapter_lines() -> None:
    events: list[RunEvent] = []

    handled = _forward_adapter_stderr_line("uvicorn warning line\n", event_sink=events.append)

    assert handled is False
    assert events == []
