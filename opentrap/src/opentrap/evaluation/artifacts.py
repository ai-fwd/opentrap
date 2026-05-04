"""Run artifact helpers for trap evaluations."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from opentrap.artifacts import (
    EVALUATION_CSV_FILE_NAME,
    EVALUATION_JSONL_FILE_NAME,
    EVALUATION_REPORT_HTML_FILE_NAME,
    EVALUATION_SUMMARY_FILE_NAME,
    write_csv_artifact,
    write_json_artifact,
    write_jsonl_artifact,
    write_text_artifact,
)


@dataclass(frozen=True)
class EvaluationArtifacts:
    """Standard paths and summary returned by trap evaluation."""

    evaluation_jsonl_path: Path
    evaluation_csv_path: Path
    evaluation_summary_path: Path
    evaluation_report_html_path: Path | None
    summary: Any


def load_run_manifest(run_manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{run_manifest_path} must contain a JSON object")
    return payload


def require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"run manifest field '{key}' must be a non-empty string")
    return value


def find_trap_entry(payload: Mapping[str, Any], *, trap_id: str) -> dict[str, Any]:
    traps_raw = payload.get("traps")
    if not isinstance(traps_raw, list):
        raise RuntimeError("run manifest field 'traps' must be a list")
    for entry in traps_raw:
        if isinstance(entry, dict) and entry.get("trap_id") == trap_id:
            return entry
    raise RuntimeError(f"trap '{trap_id}' was not found in run manifest")


def load_observed_outputs(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}

    observed_by_case_index: dict[int, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            continue
        case_index = value.get("case_index")
        content = value.get("content")
        if not isinstance(case_index, int) or case_index < 0:
            continue
        if not isinstance(content, str):
            continue
        observed_by_case_index[case_index] = content
    return observed_by_case_index


def write_evaluation_artifacts(
    *,
    run_dir: Path,
    records: Sequence[Any],
    summary: Any,
    csv_fieldnames: Sequence[str],
    record_to_payload: Callable[[Any], Mapping[str, Any]] | None = None,
    csv_exclude_fields: set[str] | None = None,
    evaluation_report_html: str | None = None,
) -> EvaluationArtifacts:
    evaluation_jsonl_path = run_dir / EVALUATION_JSONL_FILE_NAME
    evaluation_csv_path = run_dir / EVALUATION_CSV_FILE_NAME
    evaluation_summary_path = run_dir / EVALUATION_SUMMARY_FILE_NAME
    evaluation_report_html_path = None

    write_jsonl_records(evaluation_jsonl_path, records, record_to_payload=record_to_payload)
    write_csv_records(
        evaluation_csv_path,
        records,
        fieldnames=csv_fieldnames,
        record_to_payload=record_to_payload,
        exclude_fields=csv_exclude_fields or set(),
    )
    summary_payload = to_json_payload(summary)
    if not isinstance(summary_payload, Mapping):
        raise RuntimeError("evaluation summary payload must be a mapping")
    write_json_artifact(evaluation_summary_path, summary_payload)
    if isinstance(evaluation_report_html, str):
        evaluation_report_html_path = run_dir / EVALUATION_REPORT_HTML_FILE_NAME
        write_text_artifact(evaluation_report_html_path, evaluation_report_html)

    return EvaluationArtifacts(
        evaluation_jsonl_path=evaluation_jsonl_path,
        evaluation_csv_path=evaluation_csv_path,
        evaluation_summary_path=evaluation_summary_path,
        evaluation_report_html_path=evaluation_report_html_path,
        summary=summary,
    )


def write_jsonl_records(
    path: Path,
    records: Sequence[Any],
    *,
    record_to_payload: Callable[[Any], Mapping[str, Any]] | None = None,
) -> None:
    write_jsonl_artifact(
        path,
        (_record_payload(record, record_to_payload=record_to_payload) for record in records),
    )


def write_csv_records(
    path: Path,
    records: Sequence[Any],
    *,
    fieldnames: Sequence[str],
    record_to_payload: Callable[[Any], Mapping[str, Any]] | None = None,
    exclude_fields: set[str] | None = None,
) -> None:
    excluded = exclude_fields or set()
    csv_fields = [field for field in fieldnames if field not in excluded]
    write_csv_artifact(
        path,
        (_record_payload(record, record_to_payload=record_to_payload) for record in records),
        fieldnames=csv_fields,
    )


def to_json_payload(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _record_payload(
    record: Any,
    *,
    record_to_payload: Callable[[Any], Mapping[str, Any]] | None,
) -> Mapping[str, Any]:
    if record_to_payload is not None:
        return record_to_payload(record)
    payload = to_json_payload(record)
    if not isinstance(payload, Mapping):
        raise RuntimeError("evaluation record payload must be a mapping")
    return payload
