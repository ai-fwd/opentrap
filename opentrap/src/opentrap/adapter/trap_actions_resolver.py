from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import ManifestView


@contextmanager
def _prepend_sys_path(path: Path) -> Iterator[None]:
    original = list(sys.path)
    sys.path.insert(0, str(path))
    try:
        yield
    finally:
        sys.path[:] = original


def _load_module(module_path: Path):
    module_name = f"opentrap_dynamic_actions_{abs(hash(str(module_path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module spec from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def resolve_trap_actions(manifest: ManifestView) -> object | None:
    if not manifest.traps:
        return None

    trap = manifest.traps[0]
    if trap.trap_id != "perception/prompt_injection_via_html":
        return None
    if trap.data_dir is None:
        raise RuntimeError("trap 'perception/prompt_injection_via_html' is missing data_dir")

    candidate_dirs = (
        manifest.repo_root
        / "opentrap"
        / "src"
        / "traps"
        / "perception"
        / "prompt_injection_via_html",
        Path(__file__).resolve().parents[2]
        / "traps"
        / "perception"
        / "prompt_injection_via_html",
    )
    trap_dir = next((path for path in candidate_dirs if path.exists()), candidate_dirs[0])
    actions_path = trap_dir / "actions.py"
    if not actions_path.exists():
        raise RuntimeError(f"trap actions module not found at {actions_path}")

    with _prepend_sys_path(trap_dir):
        module = _load_module(actions_path)

    trap_actions = getattr(module, "TrapActions", None)
    if trap_actions is None:
        raise RuntimeError(f"{actions_path} must define TrapActions")

    return trap_actions(data_dir=trap.data_dir)
