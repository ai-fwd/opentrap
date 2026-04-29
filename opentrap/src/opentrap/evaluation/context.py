"""Evaluation context normalization shared by trap implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvaluationContext:
    """Normalized context passed from orchestration into `Trap.evaluate(...)`."""

    run_manifest_path: Path
    run_dir: Path
    report_path: Path
    trap_id: str
    status_emitter: object | None = None

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        default_trap_id: str,
    ) -> EvaluationContext:
        if isinstance(value, EvaluationContext):
            return value
        if not isinstance(value, Mapping):
            raise RuntimeError("trap evaluation context must be a mapping")

        run_manifest_path = cls._read_path(value, "run_manifest_path")
        run_dir = cls._read_optional_path(value, "run_dir") or run_manifest_path.parent
        report_path = cls._read_optional_path(value, "report_path") or (run_dir / "report.json")
        trap_id_value = value.get("trap_id")
        trap_id = (
            trap_id_value
            if isinstance(trap_id_value, str) and trap_id_value
            else default_trap_id
        )

        return cls(
            run_manifest_path=run_manifest_path,
            run_dir=run_dir,
            report_path=report_path,
            trap_id=trap_id,
            status_emitter=value.get("status_emitter"),
        )

    @staticmethod
    def _read_path(value: Mapping[str, Any], key: str) -> Path:
        raw = value.get(key)
        if not isinstance(raw, str) or not raw:
            raise RuntimeError(f"trap evaluation context field '{key}' must be a non-empty string")
        return Path(raw)

    @staticmethod
    def _read_optional_path(value: Mapping[str, Any], key: str) -> Path | None:
        raw = value.get(key)
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw:
            raise RuntimeError(f"trap evaluation context field '{key}' must be a non-empty string")
        return Path(raw)
