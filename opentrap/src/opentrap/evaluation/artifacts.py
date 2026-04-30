"""Run artifact helpers for trap evaluations."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any


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
    evaluation_jsonl_path = run_dir / "evaluation.jsonl"
    evaluation_csv_path = run_dir / "evaluation.csv"
    evaluation_summary_path = run_dir / "evaluation_summary.json"
    evaluation_report_html_path = None

    write_jsonl_records(evaluation_jsonl_path, records, record_to_payload=record_to_payload)
    write_csv_records(
        evaluation_csv_path,
        records,
        fieldnames=csv_fieldnames,
        record_to_payload=record_to_payload,
        exclude_fields=csv_exclude_fields or set(),
    )
    evaluation_summary_path.write_text(
        json.dumps(to_json_payload(summary), indent=2) + "\n",
        encoding="utf-8",
    )
    if isinstance(evaluation_report_html, str):
        evaluation_report_html_path = run_dir / "evaluation_report.html"
        evaluation_report_html_path.write_text(evaluation_report_html, encoding="utf-8")

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
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            payload = _record_payload(record, record_to_payload=record_to_payload)
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


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
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for record in records:
            payload = dict(_record_payload(record, record_to_payload=record_to_payload))
            row = {field: _csv_value(payload.get(field)) for field in csv_fields}
            writer.writerow(row)


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


def _csv_value(value: Any) -> Any:
    if isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, str | bytes)
    ):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value
