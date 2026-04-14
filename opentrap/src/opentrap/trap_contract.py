from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

FieldType = Literal["string", "integer", "number", "boolean"]
MISSING_DEFAULT = object()


@dataclass(frozen=True)
class SharedConfig:
    scenario: str
    content_type: str
    attack_intent: str
    seed: int | None


@dataclass(frozen=True)
class TrapFieldSpec:
    type: FieldType
    required: bool = False
    default: object = MISSING_DEFAULT
    min: float | int | None = None
    max: float | int | None = None
    min_length: int | None = None
    allowed_values: tuple[Any, ...] | None = None
    description: str = ""

    def has_default(self) -> bool:
        return self.default is not MISSING_DEFAULT


TrapRunFn = Callable[[SharedConfig, Mapping[str, Any], Path], Path]


@dataclass(frozen=True)
class TrapSpec:
    trap_id: str
    fields: Mapping[str, TrapFieldSpec]
    run: TrapRunFn
