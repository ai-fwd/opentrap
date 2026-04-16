"""Dataset fingerprinting and cache publication for trap generation runs.

This module owns the logic that turns trap inputs into deterministic cache keys and
publishes generated artifacts into stable cache directories so repeated runs can
reuse prior datasets.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentrap.io_utils import load_json_maybe, utc_now_iso, write_json
from opentrap.trap_contract import SharedConfig, TrapSpec

CACHE_WAIT_TIMEOUT_SECONDS = 2.0
CACHE_WAIT_POLL_INTERVAL_SECONDS = 0.05
DATASET_FINGERPRINT_VERSION = "v1"


@dataclass(frozen=True)
class DatasetSnapshot:
    """Resolved dataset metadata consumed by run manifest assembly."""

    dataset_fingerprint: str
    dataset_cache_dir: str
    dataset_source: str
    artifact_path: str
    metadata_path: str
    data_dir: str
    data_items: list[dict[str, str]]

    def as_manifest_fields(self) -> dict[str, Any]:
        """Render snapshot fields in the shape expected by trap manifest entries."""
        return {
            "dataset_fingerprint": self.dataset_fingerprint,
            "dataset_cache_dir": self.dataset_cache_dir,
            "dataset_source": self.dataset_source,
            "artifact_path": self.artifact_path,
            "metadata_path": self.metadata_path,
            "data_dir": self.data_dir,
            "data_items": self.data_items,
        }


def _canonical_json_bytes(payload: Any) -> bytes:
    """Encode JSON payload into deterministic bytes for hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256_hex_bytes(payload: bytes) -> str:
    """Return SHA-256 hex digest for a raw byte payload."""
    return hashlib.sha256(payload).hexdigest()


def _sha256_hex_text(payload: str) -> str:
    """Return SHA-256 hex digest for UTF-8 text content."""
    return _sha256_hex_bytes(payload.encode("utf-8"))


def _build_dataset_fingerprint(
    trap_id: str,
    shared: SharedConfig,
    trap_config: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Create the deterministic fingerprint payload and digest for a trap run input."""
    sample_records = [
        {"path": sample.path, "content_sha256": _sha256_hex_text(sample.content)}
        for sample in sorted(shared.samples, key=lambda item: item.path)
    ]

    fingerprint_payload: dict[str, Any] = {
        "version": DATASET_FINGERPRINT_VERSION,
        "trap_id": trap_id,
        "shared": {
            "scenario": shared.scenario,
            "content_style": shared.content_style,
            "trap_intent": shared.trap_intent,
            "seed": shared.seed,
        },
        "trap_config": dict(trap_config),
        "samples": sample_records,
    }
    fingerprint = _sha256_hex_bytes(_canonical_json_bytes(fingerprint_payload))
    return fingerprint, fingerprint_payload


def _dataset_cache_dir(dataset_dir: Path, trap_id: str, fingerprint: str) -> Path:
    """Build the trap/fingerprint cache folder path."""
    trap_segments = trap_id.split("/")
    return dataset_dir.joinpath(*trap_segments, fingerprint)


def _normalize_data_items(raw: Any) -> list[dict[str, str]]:
    """Normalize persisted data-item records into id/path dictionaries."""
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        path = item.get("path")
        if not isinstance(item_id, str) or not isinstance(path, str):
            continue
        normalized.append({"id": item_id, "path": path})
    return normalized


def _extract_data_items(artifact_path: Path) -> list[dict[str, str]]:
    """Extract data items from artifact metadata, falling back to `data/` files."""
    if artifact_path.is_file():
        return []

    metadata_path = artifact_path / "metadata.jsonl"
    data_dir = artifact_path / "data"
    collected: list[dict[str, str]] = []

    if metadata_path.exists():
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            item_id = record.get("file_id")
            filename = record.get("filename")
            if not isinstance(item_id, str) or not isinstance(filename, str):
                continue
            item_path = data_dir / filename
            collected.append({"id": item_id, "path": str(item_path)})
        if collected:
            return collected

    if data_dir.exists():
        for file_path in sorted(path for path in data_dir.iterdir() if path.is_file()):
            collected.append({"id": file_path.stem, "path": str(file_path)})

    return collected


def _read_cached_dataset_snapshot(cache_dir: Path) -> DatasetSnapshot | None:
    """Load dataset snapshot when cache metadata and artifact are fully available."""
    cache_metadata_path = cache_dir / "cache.json"
    artifact_path = cache_dir / "artifact"

    if not cache_metadata_path.exists() or not artifact_path.exists():
        return None

    cache_metadata = load_json_maybe(cache_metadata_path)
    if cache_metadata is None:
        return None

    data_items = _normalize_data_items(cache_metadata.get("data_items"))
    if not data_items:
        data_items = _extract_data_items(artifact_path)

    return DatasetSnapshot(
        dataset_fingerprint=str(cache_metadata.get("dataset_fingerprint", "")),
        dataset_cache_dir=str(cache_dir),
        dataset_source="cache_hit",
        artifact_path=str(artifact_path),
        metadata_path=str(artifact_path / "metadata.jsonl"),
        data_dir=str(artifact_path / "data"),
        data_items=data_items,
    )


def _wait_for_cached_dataset_snapshot(cache_dir: Path) -> DatasetSnapshot | None:
    """Poll for a cache snapshot while another process may be publishing it."""
    deadline = time.monotonic() + CACHE_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        snapshot = _read_cached_dataset_snapshot(cache_dir)
        if snapshot is not None:
            return snapshot
        time.sleep(CACHE_WAIT_POLL_INTERVAL_SECONDS)
    return None


def resolve_cached_dataset(
    *,
    trap_id: str,
    trap_slug: str,
    shared: SharedConfig,
    trap_config: Mapping[str, Any],
    registry: Mapping[str, TrapSpec],
    dataset_dir: Path,
) -> DatasetSnapshot:
    """Resolve dataset snapshot by reusing cache or generating and publishing once.

    Returns:
        DatasetSnapshot with artifact pointers and cache identity metadata.

    Raises:
        RuntimeError: Cache is unavailable after generation/publish attempts.
    """
    fingerprint, fingerprint_payload = _build_dataset_fingerprint(trap_id, shared, trap_config)
    cache_dir = _dataset_cache_dir(dataset_dir, trap_id, fingerprint)

    cached_snapshot = _read_cached_dataset_snapshot(cache_dir)
    if cached_snapshot is not None:
        return DatasetSnapshot(
            dataset_fingerprint=fingerprint,
            dataset_cache_dir=cached_snapshot.dataset_cache_dir,
            dataset_source="cache_hit",
            artifact_path=cached_snapshot.artifact_path,
            metadata_path=cached_snapshot.metadata_path,
            data_dir=cached_snapshot.data_dir,
            data_items=cached_snapshot.data_items,
        )

    tmp_root = dataset_dir / "_tmp" / uuid.uuid4().hex
    output_base = tmp_root / "output" / trap_slug
    staging_dir = tmp_root / "staging"
    staging_dir.mkdir(parents=True, exist_ok=False)

    published = False
    try:
        generated_artifact = registry[trap_id].run(shared, dict(trap_config), output_base)
        staged_artifact = staging_dir / "artifact"
        generated_artifact.replace(staged_artifact)

        dataset_items = _extract_data_items(staged_artifact)
        cache_payload = {
            "version": DATASET_FINGERPRINT_VERSION,
            "trap_id": trap_id,
            "dataset_fingerprint": fingerprint,
            "created_at_utc": utc_now_iso(),
            "fingerprint_payload": fingerprint_payload,
            "data_items": dataset_items,
        }
        write_json(staging_dir / "cache.json", cache_payload)

        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            staging_dir.replace(cache_dir)
            published = True
        except FileExistsError:
            published = False
    finally:
        if not published:
            shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(tmp_root, ignore_errors=True)

    cached_snapshot = _read_cached_dataset_snapshot(cache_dir)
    if cached_snapshot is None:
        cached_snapshot = _wait_for_cached_dataset_snapshot(cache_dir)
    if cached_snapshot is None:
        raise RuntimeError(f"cached dataset is unavailable at {cache_dir}")

    dataset_source = "generated_then_cached" if published else "cache_hit"
    return DatasetSnapshot(
        dataset_fingerprint=fingerprint,
        dataset_cache_dir=cached_snapshot.dataset_cache_dir,
        dataset_source=dataset_source,
        artifact_path=cached_snapshot.artifact_path,
        metadata_path=cached_snapshot.metadata_path,
        data_dir=cached_snapshot.data_dir,
        data_items=cached_snapshot.data_items,
    )
