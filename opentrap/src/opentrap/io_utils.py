"""Shared JSON/time helpers used by CLI and runtime orchestration paths.

This module centralizes file IO patterns so run manifests, reports, and cache metadata
are written consistently and safely across commands and session lifecycle flows.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(tz=UTC).isoformat()


def write_json(path: Path, payload: dict[str, Any], *, atomic: bool = False) -> None:
    """Serialize a JSON object to disk.

    Args:
        path: Target JSON path.
        payload: Mapping payload to serialize.
        atomic: When True, write through a temporary sibling file and replace.
    """
    content = json.dumps(payload, indent=2) + "\n"
    if not atomic:
        path.write_text(content, encoding="utf-8")
        return

    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    """Load a required JSON object from disk.

    Raises:
        RuntimeError: The JSON root is not an object.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return raw


def load_json_maybe(path: Path) -> dict[str, Any] | None:
    """Best-effort JSON object load used for polling and optional snapshots."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def write_jsonl(
    path: Path,
    rows: Iterable[dict[str, Any]],
    *,
    atomic: bool = False,
) -> None:
    """Serialize JSON-object rows to newline-delimited JSON."""
    content = "".join(json.dumps(row) + "\n" for row in rows)
    if not atomic:
        path.write_text(content, encoding="utf-8")
        return

    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one JSON-object row to a newline-delimited JSON file."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load newline-delimited JSON objects from disk."""
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(f"{path} must contain JSON objects per line")
        rows.append(value)
    return rows
