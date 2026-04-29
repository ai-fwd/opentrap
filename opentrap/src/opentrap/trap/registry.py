from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from opentrap.trap.contract import TrapFieldSpec, TrapSpec


class TrapRegistryError(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(self._render())

    def _render(self) -> str:
        lines = ["Failed to build trap registry:"]
        lines.extend(f"- {error}" for error in self.errors)
        return "\n".join(lines)


def discover_trap_candidates(traps_dir: Path) -> list[tuple[str, Path]]:
    if not traps_dir.exists():
        return []

    candidates: list[tuple[str, Path]] = []
    for target_dir in sorted(
        (path for path in traps_dir.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.name,
    ):
        for trap_dir in sorted(
            (
                path
                for path in target_dir.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ),
            key=lambda path: path.name,
        ):
            if trap_dir.name == "__pycache__":
                continue
            trap_id = f"{target_dir.name}/{trap_dir.name}"
            candidates.append((trap_id, trap_dir))
    return candidates


@contextmanager
def _prepend_sys_path(path: Path):
    original = list(sys.path)
    sys.path.insert(0, str(path))
    try:
        yield
    finally:
        sys.path[:] = original


def _load_trap_module(module_path: Path):
    module_name = f"opentrap_dynamic_trap_{abs(hash(str(module_path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module spec from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class _TrapEntry:
    trap_id: str
    trap_path: Path


class TrapRegistry:
    def __init__(self, entries: Mapping[str, _TrapEntry]) -> None:
        self._entries = dict(entries)
        self._trap_class_cache: dict[str, type[TrapSpec[Any, Any, Any, Any]]] = {}

    @property
    def trap_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def has_trap(self, trap_id: str) -> bool:
        return trap_id in self._entries

    def load_trap_class(self, trap_id: str) -> type[TrapSpec[Any, Any, Any, Any]]:
        if trap_id in self._trap_class_cache:
            return self._trap_class_cache[trap_id]

        entry = self._entries.get(trap_id)
        if entry is None:
            raise TrapRegistryError([f"{trap_id}: trap id was not discovered"])

        try:
            with _prepend_sys_path(entry.trap_path.parent):
                module = _load_trap_module(entry.trap_path)
        except Exception as exc:  # noqa: BLE001
            raise TrapRegistryError([f"{trap_id}: Trap() failed during import ({exc})"]) from exc

        trap_class = getattr(module, "Trap", None)
        if trap_class is None or not isinstance(trap_class, type):
            raise TrapRegistryError([f"{trap_id}: trap.py must define class Trap"])
        if not issubclass(trap_class, TrapSpec):
            raise TrapRegistryError([f"{trap_id}: Trap must inherit TrapSpec"])

        typed_class = cast(type[TrapSpec[Any, Any, Any, Any]], trap_class)
        self._trap_class_cache[trap_id] = typed_class
        return typed_class

    def load_trap_fields(self, trap_id: str) -> Mapping[str, TrapFieldSpec]:
        trap_class = self.load_trap_class(trap_id)
        fields = getattr(trap_class, "fields", None)
        if not isinstance(fields, Mapping):
            raise TrapRegistryError([f"{trap_id}: Trap.fields must be a mapping"])
        return cast(Mapping[str, TrapFieldSpec], fields)

    def create_trap(self, trap_id: str) -> TrapSpec[Any, Any, Any, Any]:
        trap_class = self.load_trap_class(trap_id)
        try:
            trap = trap_class()
        except Exception as exc:  # noqa: BLE001
            raise TrapRegistryError([f"{trap_id}: Trap() failed ({exc})"]) from exc
        trap.trap_id = trap_id
        return trap


def build_trap_registry(traps_dir: Path) -> TrapRegistry:
    errors: list[str] = []
    entries: dict[str, _TrapEntry] = {}

    for trap_id, trap_dir in discover_trap_candidates(traps_dir):
        trap_path = trap_dir / "trap.py"
        if not trap_path.exists():
            errors.append(f"{trap_id}: missing trap.py")
            continue
        entries[trap_id] = _TrapEntry(trap_id=trap_id, trap_path=trap_path)

    if errors:
        raise TrapRegistryError(errors)

    return TrapRegistry(entries)
