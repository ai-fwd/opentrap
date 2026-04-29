from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar

ConfigT = TypeVar("ConfigT", contravariant=True)


class TrapDefinition(Protocol[ConfigT]):
    trap_id: str

    def run(self, config: ConfigT, output_base: Path) -> Path:
        ...
