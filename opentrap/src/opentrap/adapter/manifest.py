from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .models import DataItemView, ManifestTrapView, ManifestView, _RuntimeMetadata


def load_manifest_payload(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("manifest payload must be a JSON object")
    return payload


def resolve_repo_root(payload: Mapping[str, Any]) -> Path:
    repo_root = payload.get("repo_root")
    if isinstance(repo_root, str) and repo_root.strip():
        return Path(repo_root)
    return Path.cwd()


def resolve_product_under_test(payload: Mapping[str, Any]) -> str:
    raw_product = payload.get("product_under_test")
    if raw_product is None:
        return "default"
    if not isinstance(raw_product, str) or not raw_product.strip():
        raise RuntimeError("manifest.product_under_test must be a non-empty string when present")
    product = raw_product.strip()
    if product in {".", ".."} or "/" in product or "\\" in product:
        raise RuntimeError("manifest.product_under_test must not contain path separators")
    return product


def resolve_manifest_path(path_value: object, *, repo_root: Path) -> Path | None:
    if not isinstance(path_value, str) or not path_value.strip():
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    return repo_root / path


def load_manifest_data_items(
    trap_payload: Mapping[str, Any],
    *,
    repo_root: Path,
) -> tuple[DataItemView, ...]:
    raw_items = trap_payload.get("data_items")
    if not isinstance(raw_items, list):
        return ()

    items: list[DataItemView] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item_id = raw_item.get("id")
        path_value = raw_item.get("path")
        if not isinstance(item_id, str) or not item_id:
            continue
        resolved_path = resolve_manifest_path(path_value, repo_root=repo_root)
        if resolved_path is None:
            continue
        items.append(DataItemView(id=item_id, path=resolved_path))
    return tuple(items)


def load_manifest_view(manifest_path: Path, payload: Mapping[str, Any]) -> ManifestView:
    repo_root = resolve_repo_root(payload)
    requested = payload.get("requested")
    requested_value = requested if isinstance(requested, str) and requested else None

    traps_payload = payload.get("traps")
    traps: list[ManifestTrapView] = []
    if isinstance(traps_payload, list):
        for raw_trap in traps_payload:
            if not isinstance(raw_trap, dict):
                continue
            trap_id = raw_trap.get("trap_id")
            if not isinstance(trap_id, str) or not trap_id:
                continue
            traps.append(
                ManifestTrapView(
                    trap_id=trap_id,
                    artifact_path=resolve_manifest_path(
                        raw_trap.get("artifact_path"),
                        repo_root=repo_root,
                    ),
                    metadata_path=resolve_manifest_path(
                        raw_trap.get("metadata_path"),
                        repo_root=repo_root,
                    ),
                    data_dir=resolve_manifest_path(
                        raw_trap.get("data_dir"),
                        repo_root=repo_root,
                    ),
                    data_items=load_manifest_data_items(raw_trap, repo_root=repo_root),
                    cases=tuple(
                        dict(case)
                        for case in raw_trap.get("cases", [])
                        if isinstance(case, dict)
                    ),
                )
            )

    return ManifestView(
        manifest_path=manifest_path,
        repo_root=repo_root,
        requested=requested_value,
        traps=tuple(traps),
    )


def load_manifest_metadata(manifest_path: Path) -> _RuntimeMetadata:
    payload = load_manifest_payload(manifest_path)

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("manifest.run_id must be a non-empty string")

    manifest = load_manifest_view(manifest_path, payload)
    trap_ids = tuple(trap.trap_id for trap in manifest.traps)

    return _RuntimeMetadata(run_id=run_id, trap_ids=trap_ids, manifest=manifest)


def generated_adapter_dir(manifest_path: Path) -> tuple[str, Path]:
    payload = load_manifest_payload(manifest_path)
    repo_root = resolve_repo_root(payload)
    product = resolve_product_under_test(payload)
    return product, repo_root / "adapter" / "generated" / product
