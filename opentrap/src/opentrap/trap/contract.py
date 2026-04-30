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
class TrapCaseContext:
    artifact_path: Path
    metadata_path: Path
    data_dir: Path
    data_items: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class TrapGenerationCounts:
    generated_artifacts: int
    base_cases: int
    variant_cases: int

    def total_cases(self) -> int:
        return self.base_cases + self.variant_cases


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


BindContextT = TypeVar("BindContextT")
ActionsT = TypeVar("ActionsT")
EvalContextT = TypeVar("EvalContextT")
EvalResultT = TypeVar("EvalResultT")


class TrapSpec(ABC, Generic[BindContextT, ActionsT, EvalContextT, EvalResultT]):
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
    def bind(self, context: BindContextT) -> ActionsT:
        """Attach runtime context and return trap actions for adapter handlers."""

    @abstractmethod
    def build_cases(self, context: TrapCaseContext) -> list[dict[str, Any]]:
        """Parse generated trap artifacts into ordered execution cases."""

    @abstractmethod
    def generation_counts(self, context: TrapCaseContext) -> TrapGenerationCounts:
        """Return generated-artifact and base/variant case counts for CLI/reporting."""

    @abstractmethod
    def evaluate(self, context: EvalContextT) -> EvalResultT:
        """Evaluate one finalized run/session context and return trap-specific scoring output."""
