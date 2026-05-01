"""Shared trap evaluation result contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvaluationResult:
    """Minimum normalized trap-evaluation outcome consumed by OpenTrap core."""

    success_count: int
    evaluated_count: int
    evaluation_errors: int = 0
    details: Mapping[str, Any] | None = None

    def validate(self) -> None:
        if self.success_count < 0:
            raise RuntimeError("EvaluationResult.success_count must be >= 0")
        if self.evaluated_count < 0:
            raise RuntimeError("EvaluationResult.evaluated_count must be >= 0")
        if self.success_count > self.evaluated_count:
            raise RuntimeError(
                "EvaluationResult.success_count must be <= EvaluationResult.evaluated_count"
            )
        if self.evaluation_errors < 0:
            raise RuntimeError("EvaluationResult.evaluation_errors must be >= 0")
        if self.details is not None and not isinstance(self.details, Mapping):
            raise RuntimeError("EvaluationResult.details must be a mapping when provided")
