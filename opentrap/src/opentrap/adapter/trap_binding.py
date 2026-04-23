from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from opentrap.trap_registry import TrapRegistryError
from opentrap.trap_registry_loader import load_registry_from_candidates

from .models import ManifestTrapView, ManifestView


def _candidate_traps_dirs(manifest: ManifestView) -> tuple[Path, Path]:
    return (
        manifest.repo_root / "opentrap" / "src" / "traps",
        Path(__file__).resolve().parents[2] / "traps",
    )


def _build_bind_context(*, manifest: ManifestView, trap: ManifestTrapView) -> SimpleNamespace:
    return SimpleNamespace(
        trap_id=trap.trap_id,
        artifact_path=trap.artifact_path,
        metadata_path=trap.metadata_path,
        data_dir=trap.data_dir,
        data_items=trap.data_items,
        manifest=manifest,
        repo_root=manifest.repo_root,
        manifest_path=manifest.manifest_path,
    )


def resolve_trap_actions(manifest: ManifestView) -> object | None:
    if not manifest.traps:
        return None

    manifest_trap = manifest.traps[0]
    try:
        registry = load_registry_from_candidates(_candidate_traps_dirs(manifest))
    except TrapRegistryError as exc:
        raise RuntimeError(
            f"failed to load trap registry for adapter runtime: {exc}"
        ) from exc
    if registry is None or not registry.has_trap(manifest_trap.trap_id):
        return None

    try:
        trap = registry.create_trap(manifest_trap.trap_id)
    except TrapRegistryError as exc:
        raise RuntimeError(
            f"failed to initialize trap '{manifest_trap.trap_id}' for adapter binding: {exc}"
        ) from exc

    bind_context = _build_bind_context(manifest=manifest, trap=manifest_trap)
    try:
        return trap.bind(bind_context)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"failed to bind trap actions for '{manifest_trap.trap_id}': {exc}"
        ) from exc
