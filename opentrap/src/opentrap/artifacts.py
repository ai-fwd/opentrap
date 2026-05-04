"""Canonical artifact names and serialization helpers."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

RUN_MANIFEST_FILE_NAME = "run.json"
REPORT_FILE_NAME = "report.json"
SESSIONS_FILE_NAME = "sessions.jsonl"
TRACES_FILE_NAME = "traces.jsonl"
OBSERVATIONS_FILE_NAME = "observations.jsonl"
ACTIVE_SESSION_FILE_NAME = "active_session.json"
DATASET_METADATA_FILE_NAME = "metadata.jsonl"
CACHE_DESCRIPTOR_FILE_NAME = "cache.json"
EVALUATION_JSONL_FILE_NAME = "evaluation.jsonl"
EVALUATION_CSV_FILE_NAME = "evaluation.csv"
EVALUATION_SUMMARY_FILE_NAME = "evaluation_summary.json"
EVALUATION_REPORT_HTML_FILE_NAME = "evaluation_report.html"


def write_json_artifact(
    path: Path,
    payload: Mapping[str, Any],
    *,
    atomic: bool = True,
) -> None:
    """Write a JSON object artifact with consistent UTF-8 formatting."""
    _ensure_mapping(payload, artifact_type="JSON")
    content = json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n"
    _write_text(path, content, atomic=atomic)


def write_jsonl_artifact(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    atomic: bool = True,
) -> None:
    """Write a whole JSONL artifact from object rows."""
    content = "".join(json.dumps(_row(row), ensure_ascii=False) + "\n" for row in rows)
    _write_text(path, content, atomic=atomic)


def append_jsonl_artifact(path: Path, row: Mapping[str, Any]) -> None:
    """Append one object row to a streaming JSONL artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_row(row), ensure_ascii=False) + "\n")


def write_csv_artifact(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str],
    atomic: bool = True,
) -> None:
    """Write a CSV artifact with explicit field ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target_path = _temp_path(path) if atomic else path
    with target_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            payload = _row(row)
            writer.writerow({field: _csv_value(payload.get(field)) for field in fieldnames})
    if atomic:
        target_path.replace(path)


def write_text_artifact(path: Path, content: str, *, atomic: bool = True) -> None:
    """Write a standalone text artifact, including HTML reports."""
    if not isinstance(content, str):
        raise RuntimeError("text artifact content must be a string")
    _write_text(path, content, atomic=atomic)


def _write_text(path: Path, content: str, *, atomic: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not atomic:
        path.write_text(content, encoding="utf-8")
        return
    temp_path = _temp_path(path)
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _temp_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".tmp")


def _ensure_mapping(value: object, *, artifact_type: str) -> None:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{artifact_type} artifact payload must be a mapping")


def _row(row: Mapping[str, Any]) -> dict[str, Any]:
    _ensure_mapping(row, artifact_type="JSONL/CSV row")
    return dict(row)


def _csv_value(value: Any) -> Any:
    if isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, str | bytes)
    ):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value
