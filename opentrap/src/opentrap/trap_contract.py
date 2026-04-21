from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

FieldType = Literal["string", "integer", "number", "boolean"]
MISSING_DEFAULT = object()


@dataclass(frozen=True)
class SampleBoundary:
    path: str
    content: str


@dataclass(frozen=True)
class SharedConfig:
    scenario: str
    content_style: str
    trap_intent: str
    seed: int | None
    samples: tuple[SampleBoundary, ...] = ()


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


RunContextT = TypeVar("RunContextT")
ActionsT = TypeVar("ActionsT")
EvalContextT = TypeVar("EvalContextT")
EvalResultT = TypeVar("EvalResultT")


class TrapSpec(ABC, Generic[RunContextT, ActionsT, EvalContextT, EvalResultT]):
    trap_id: str
    fields: Mapping[str, TrapFieldSpec]

    @abstractmethod
    def generate(
        self,
        shared_config: SharedConfig,
        trap_config: Mapping[str, Any],
        output_base: Path,
    ) -> Path:
        """Generate trap artifact data and return the produced file/directory path."""

    @abstractmethod
    def run(self, context: RunContextT) -> ActionsT:
        """Bind runtime context and return trap actions consumed by adapter handlers."""

    @abstractmethod
    def evaluate(self, context: EvalContextT) -> EvalResultT:
        """Evaluate one finalized run/session context and return trap-specific scoring output."""
