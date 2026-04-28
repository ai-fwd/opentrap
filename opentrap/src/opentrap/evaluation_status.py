"""Reusable evaluation status utilities shared across traps.

This module centralizes human-readable evaluation progress messaging so
orchestration can provide consistent status lines regardless of trap-specific
evaluation implementations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationStatusEmitter:
    """Emit standardized evaluation phase and progress status lines.

    The emitter is intentionally lightweight and side-effect free aside from
    calling `status_callback`, so orchestration can safely pass it to traps.
    """

    status_callback: Callable[[str], None]
    heartbeat_every: int = 25
    prefix: str = "evaluation"

    def phase(self, phase: str, *, detail: str | None = None) -> None:
        token = f"{self.prefix}.{phase}"
        message = f"{token}: {detail}" if detail else token
        self.status_callback(message)

    def heartbeat(self, *, processed: int, total: int, force: bool = False) -> None:
        if total <= 0:
            return
        step = self.heartbeat_every if self.heartbeat_every > 0 else 1
        processed_count = max(0, min(processed, total))
        should_emit = force or processed_count >= total or (processed_count % step == 0)
        if not should_emit:
            return
        percent = (processed_count / total) * 100.0
        self.status_callback(
            f"{self.prefix}.progress: {processed_count}/{total} ({percent:.1f}%)"
        )
