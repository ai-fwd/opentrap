"""CLI rendering pipeline modules for OpenTrap."""

import sys

from opentrap.cli_rendering.display_state import (
    RunDisplayState,
    SecuritySummary,
    load_security_summary,
)
from opentrap.cli_rendering.event_reducer import ReduceResult, reduce_event
from opentrap.cli_rendering.plain_renderer import PlainRenderer
from opentrap.cli_rendering.rich_renderer import RichRenderer
from opentrap.cli_rendering.view_model import build_run_view_model
from opentrap.events import EventSink


def build_renderer(*, verbose: bool = False) -> EventSink:
    """Choose rich or plain renderer based on current terminal capabilities."""
    if sys.stderr.isatty() and sys.stdout.isatty():
        return RichRenderer(verbose=verbose)
    return PlainRenderer(verbose=verbose)

__all__ = [
    "RunDisplayState",
    "SecuritySummary",
    "load_security_summary",
    "ReduceResult",
    "reduce_event",
    "build_run_view_model",
    "PlainRenderer",
    "RichRenderer",
    "build_renderer",
]
