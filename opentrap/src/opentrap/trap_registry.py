from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path

from opentrap.trap_contract import TrapSpec


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


def _load_contract_module(contract_path: Path):
    module_name = f"opentrap_dynamic_contract_{abs(hash(str(contract_path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, contract_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module spec from {contract_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def build_trap_registry(traps_dir: Path) -> dict[str, TrapSpec]:
    errors: list[str] = []
    registry: dict[str, TrapSpec] = {}

    for trap_id, trap_dir in discover_trap_candidates(traps_dir):
        contract_path = trap_dir / "contract.py"
        if not contract_path.exists():
            errors.append(f"{trap_id}: missing contract.py")
            continue

        try:
            with _prepend_sys_path(trap_dir):
                module = _load_contract_module(contract_path)
            get_trap_spec = getattr(module, "get_trap_spec", None)
            if not callable(get_trap_spec):
                raise RuntimeError("contract.py must define callable get_trap_spec()")
            spec = get_trap_spec()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{trap_id}: failed to load contract ({exc})")
            continue

        if not isinstance(spec, TrapSpec):
            errors.append(f"{trap_id}: get_trap_spec() must return TrapSpec")
            continue

        if spec.trap_id != trap_id:
            errors.append(
                f"{trap_id}: trap_id mismatch in contract (got '{spec.trap_id}')"
            )
            continue

        registry[trap_id] = spec

    if errors:
        raise TrapRegistryError(errors)

    return registry
