"""Run-report normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class SecurityResult:
    """Normalized run-level security outcome written to report.json."""

    status: Literal["vulnerable", "no_successful_traps_detected", "unavailable"]
    trap_success_count: int
    trap_failure_count: int
    evaluated_count: int
    trap_success_rate: float | None
    details: Mapping[str, Any]

    @classmethod
    def from_counts(
        cls,
        *,
        success_count: int,
        evaluated_count: int,
        details: Mapping[str, Any] | None = None,
    ) -> SecurityResult:
        trap_failure_count = evaluated_count - success_count
        trap_success_rate = (success_count / evaluated_count) if evaluated_count > 0 else None
        status: Literal["vulnerable", "no_successful_traps_detected", "unavailable"]
        if evaluated_count <= 0:
            status = "unavailable"
        elif success_count > 0:
            status = "vulnerable"
        else:
            status = "no_successful_traps_detected"

        return cls(
            status=status,
            trap_success_count=success_count,
            trap_failure_count=trap_failure_count,
            evaluated_count=evaluated_count,
            trap_success_rate=trap_success_rate,
            details=dict(details) if details is not None else {},
        )

    @classmethod
    def unavailable(cls) -> SecurityResult:
        return cls(
            status="unavailable",
            trap_success_count=0,
            trap_failure_count=0,
            evaluated_count=0,
            trap_success_rate=None,
            details={},
        )

    def to_report_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trap_success_count": self.trap_success_count,
            "trap_failure_count": self.trap_failure_count,
            "evaluated_count": self.evaluated_count,
            "trap_success_rate": self.trap_success_rate,
            "details": dict(self.details),
        }
